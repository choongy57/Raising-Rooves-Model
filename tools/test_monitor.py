"""Test monitor — runs pytest and creates Google Sheets tickets for new failures.

Usage:
    python -m tools.test_monitor                  # run tests, create tickets
    python -m tools.test_monitor --dry-run        # parse only, no sheet writes
    python -m tools.test_monitor --triage-only    # re-triage all open tickets
    python -m tools.test_monitor --list           # print all open tickets
    python -m tools.test_monitor --debug
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

from shared.logging_config import setup_logging
from tools.ticket_manager import TicketManager
from tools.triage_agent import triage, triage_all_open

REPO_ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------
# Test runner
# ------------------------------------------------------------------

def run_tests(extra_args: list[str] | None = None) -> tuple[str, int]:
    """Run pytest and return (combined output, exit code)."""
    cmd = [sys.executable, "-m", "pytest", "tests/", "--tb=short", "-q", "--no-header"]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    return result.stdout + result.stderr, result.returncode


# ------------------------------------------------------------------
# Output parser
# ------------------------------------------------------------------

_FAILED_RE = re.compile(r"^FAILED (.+?) - (.+)$", re.MULTILINE)
_ERROR_RE = re.compile(r"^ERROR (.+?) - (.+)$", re.MULTILINE)


def parse_failures(output: str) -> list[dict[str, str]]:
    """Return list of {test_path, error_summary} dicts from pytest output."""
    failures = []
    for match in _FAILED_RE.finditer(output):
        failures.append({
            "test_path": match.group(1).strip(),
            "error_summary": match.group(2).strip(),
        })
    for match in _ERROR_RE.finditer(output):
        failures.append({
            "test_path": match.group(1).strip(),
            "error_summary": match.group(2).strip(),
        })
    return failures


def _infer_stage(test_path: str) -> str:
    p = test_path.lower()
    if "stage1" in p:
        return "stage1"
    if "stage2" in p:
        return "stage2"
    if "tools" in p:
        return "tools"
    if "shared" in p or "geo_utils" in p:
        return "shared"
    return "unknown"


# ------------------------------------------------------------------
# Ticket creation
# ------------------------------------------------------------------

def report_failures(
    output: str,
    manager: TicketManager,
    dry_run: bool = False,
) -> list[str]:
    """Create tickets for new failures. Returns list of created ticket IDs."""
    failures = parse_failures(output)
    if not failures:
        return []

    created_ids: list[str] = []
    for failure in failures:
        title = f"Test failure: {failure['test_path']}"

        if manager.ticket_exists(title):
            continue

        description = (
            f"**Test:** `{failure['test_path']}`\n\n"
            f"**Error:** {failure['error_summary']}\n\n"
            f"**Source:** Automated test run (`tools/test_monitor.py`)"
        )
        raw = {
            "title": title,
            "description": description,
            "stage": _infer_stage(failure["test_path"]),
        }
        updates = triage(raw)

        if dry_run:
            print(f"  [dry-run] {updates['priority']} | {updates['stage']} | {title}")
            continue

        ticket_id = manager.create_ticket(
            title=title,
            description=description,
            stage=updates["stage"],
            type=updates["type"],
            priority=updates["priority"],
            source="auto-test",
        )
        manager.update_ticket(ticket_id, status="triaged")
        created_ids.append(ticket_id)

    return created_ids


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def _print_open_tickets(manager: TicketManager) -> None:
    tickets = manager.list_open()
    if not tickets:
        print("No open tickets.")
        return
    print(f"\n{'ID':<8} {'Priority':<14} {'Stage':<10} {'Status':<12} {'Title'}")
    print("-" * 80)
    for t in sorted(tickets, key=lambda x: x.get("priority", "P9")):
        print(
            f"{t['ticket_id']:<8} {t.get('priority',''):<14} "
            f"{t.get('stage',''):<10} {t.get('status',''):<12} {t.get('title','')[:48]}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run tests and create Google Sheets tickets for failures"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Classify failures without writing to sheet")
    parser.add_argument("--triage-only", action="store_true",
                        help="Re-triage all open tickets without running tests")
    parser.add_argument("--list", action="store_true",
                        help="Print all open tickets and exit")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    log = setup_logging("test_monitor", level="DEBUG" if args.debug else "INFO")

    if args.list:
        _print_open_tickets(TicketManager())
        return

    if args.triage_only:
        manager = TicketManager()
        n = triage_all_open(manager)
        print(f"Re-triaged {n} open ticket(s).")
        return

    log.info("Running test suite...")
    output, returncode = run_tests()
    print(output)

    if returncode == 0:
        print("All tests passed. No tickets created.")
        return

    if args.dry_run:
        print("\n[dry-run] Failures that would become tickets:")
        report_failures(output, TicketManager(), dry_run=True)
        return

    manager = TicketManager()
    created = report_failures(output, manager)
    if created:
        print(f"\nCreated {len(created)} ticket(s): {', '.join(created)}")
        print("View sheet: https://docs.google.com/spreadsheets/d/"
              f"{manager._ws.spreadsheet.id}/edit")
    else:
        print("\nAll failures already have open tickets.")


if __name__ == "__main__":
    main()

"""Auto-triage rules for Raising Rooves QA tickets.

Assigns stage, type, and priority from the ticket title and description.
Rules are intentionally simple regex matches — no LLM call needed.
"""

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tools.ticket_manager import TicketManager

log = logging.getLogger(__name__)

# (regex pattern in combined title+description, assigned value)
_STAGE_RULES: list[tuple[str, str]] = [
    (r"stage1|roof_classifier|tile_downloader|building_footprint|run_stage1|gemini_osm|pitch_extractor|dsm_processor", "stage1"),
    (r"stage2|irradiance|cool_roof|barra|era5|run_stage2|temperature_processor", "stage2"),
    (r"tools/|extract_pitch|analyse_coordinate|build_footprint_index", "tools"),
    (r"shared/|geo_utils|file_io|logging_config|validation", "shared"),
]

_TYPE_RULES: list[tuple[str, str]] = [
    (r"assertionerror|assert |test_|FAILED tests/", "test_failure"),
    (r"\bunit\b|kwh|w/m2|epsg|crs|degree|area_m2|pitch_deg|absorptance|energy_saved", "data_quality"),
    (r"importerror|modulenotfounderror|attributeerror|nameerror|typeerror", "logic_bug"),
    (r"timeout|slow|performance|memory|oom", "performance"),
    (r"\.env|api_key|config|settings|missing key|not found in .env", "config"),
]

# Priority rules evaluated top-down; first match wins
_PRIORITY_RULES: list[tuple[str, str]] = [
    # Physics / unit correctness — highest severity
    (r"cool_roof_calculator|energy_saved|absorptance|physics|kwh|w/m2|unit|crs|epsg", "P1-critical"),
    # Any test failure or unhandled exception
    (r"FAILED|ERROR|exception|traceback|pipeline crash|run_stage", "P2-high"),
    # Data quality / missing output / fallback triggered
    (r"missing|not found|fallback|warning|no data|nan|none", "P3-medium"),
    # Performance, cosmetic, config
    (r"slow|performance|memory|style|format|log", "P4-low"),
]


def _first_match(rules: list[tuple[str, str]], text: str, default: str) -> str:
    for pattern, value in rules:
        if re.search(pattern, text, re.IGNORECASE):
            return value
    return default


def triage(ticket: dict[str, Any]) -> dict[str, str]:
    """Return field updates (stage, type, priority, status) for a ticket dict."""
    combined = f"{ticket.get('title', '')} {ticket.get('description', '')}"

    stage = ticket.get("stage") or _first_match(_STAGE_RULES, combined, "unknown")
    ticket_type = ticket.get("type") or _first_match(_TYPE_RULES, combined, "logic_bug")
    priority = ticket.get("priority") or _first_match(_PRIORITY_RULES, combined, "P3-medium")

    return {
        "stage": stage,
        "type": ticket_type,
        "priority": priority,
        "status": "triaged",
    }


def triage_all_open(manager: "TicketManager") -> int:
    """Re-triage every open ticket. Returns count of tickets updated."""
    open_tickets = manager.get_tickets_by_status("open")
    count = 0
    for ticket in open_tickets:
        updates = triage(ticket)
        manager.update_ticket(ticket["ticket_id"], **updates)
        log.info(
            "Triaged %s → stage=%s type=%s priority=%s",
            ticket["ticket_id"], updates["stage"], updates["type"], updates["priority"],
        )
        count += 1
    return count

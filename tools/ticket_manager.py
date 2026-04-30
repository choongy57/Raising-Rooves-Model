"""Google Sheets ticket manager for Raising Rooves QA tickets.

Tickets live in the 'Tickets' tab of the configured spreadsheet.
Auth uses the existing GWS OAuth2 credential file (same account as the MCP server).
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import gspread
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

load_dotenv()

log = logging.getLogger(__name__)

SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")
GWS_CREDS_FILE: str = os.getenv(
    "GWS_CREDS_FILE",
    str(Path.home() / ".config" / "gws" / "uni-email.json"),
)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "ticket_id",
    "title",
    "description",
    "stage",
    "type",
    "priority",
    "status",
    "assigned_to",
    "source",
    "branch",
    "commit",
    "created_at",
    "updated_at",
    "notes",
]

# 1-based column index for each header
COL: dict[str, int] = {h: i + 1 for i, h in enumerate(HEADERS)}

VALID_STAGES = {"stage1", "stage2", "tools", "shared", "infra", "unknown"}
VALID_TYPES = {"test_failure", "logic_bug", "data_quality", "performance", "config"}
VALID_PRIORITIES = {"P1-critical", "P2-high", "P3-medium", "P4-low"}
VALID_STATUSES = {"open", "triaged", "in_progress", "review", "closed"}
ACTIVE_STATUSES = {"open", "triaged", "in_progress"}


def _build_credentials() -> Credentials:
    creds_path = Path(GWS_CREDS_FILE)
    if not creds_path.exists():
        raise FileNotFoundError(
            f"GWS credentials not found at {creds_path}. "
            "Set GWS_CREDS_FILE in .env to the correct path."
        )
    with open(creds_path) as f:
        d = json.load(f)
    creds = Credentials(
        token=None,
        refresh_token=d["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=d["client_id"],
        client_secret=d["client_secret"],
        scopes=_SCOPES,
    )
    creds.refresh(Request())
    return creds


class TicketManager:
    """CRUD interface for the Raising Rooves ticket sheet."""

    def __init__(self) -> None:
        if not SHEET_ID:
            raise ValueError(
                "GOOGLE_SHEET_ID is not set. Add it to .env."
            )
        gc = gspread.authorize(_build_credentials())
        spreadsheet = gc.open_by_key(SHEET_ID)
        try:
            self._ws = spreadsheet.worksheet("Tickets")
        except gspread.WorksheetNotFound:
            self._ws = spreadsheet.add_worksheet(
                title="Tickets", rows=1000, cols=len(HEADERS)
            )
            log.info("Created 'Tickets' worksheet")
        self._ensure_headers()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_headers(self) -> None:
        first_row = self._ws.row_values(1)
        if first_row != HEADERS:
            self._ws.insert_row(HEADERS, 1)
            log.info("Initialised ticket sheet headers")

    def _next_id(self) -> str:
        all_ids = self._ws.col_values(COL["ticket_id"])[1:]  # skip header
        nums = [
            int(t.replace("RR-", ""))
            for t in all_ids
            if t.startswith("RR-") and t[3:].isdigit()
        ]
        return f"RR-{(max(nums, default=0) + 1):03d}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_ticket(
        self,
        title: str,
        description: str,
        stage: str = "unknown",
        type: str = "logic_bug",
        priority: str = "P3-medium",
        source: str = "manual",
        assigned_to: str = "",
        notes: str = "",
    ) -> str:
        """Append a new ticket row. Returns the new ticket_id."""
        ticket_id = self._next_id()
        now = self._now()
        row = [
            ticket_id, title, description, stage, type, priority,
            "open", assigned_to, source, "", "", now, now, notes,
        ]
        self._ws.append_row(row, value_input_option="USER_ENTERED")
        log.info("Created %s: %s", ticket_id, title)
        return ticket_id

    def update_ticket(self, ticket_id: str, **kwargs: Any) -> None:
        """Update one or more fields on an existing ticket."""
        cell = self._ws.find(ticket_id, in_column=COL["ticket_id"])
        if cell is None:
            raise ValueError(f"Ticket {ticket_id} not found in sheet")
        kwargs["updated_at"] = self._now()
        for field, value in kwargs.items():
            if field in COL:
                self._ws.update_cell(cell.row, COL[field], value)
        log.info("Updated %s: %s", ticket_id, sorted(kwargs.keys()))

    def get_all_tickets(self) -> list[dict[str, Any]]:
        return self._ws.get_all_records()

    def get_tickets_by_status(self, status: str) -> list[dict[str, Any]]:
        return [t for t in self.get_all_tickets() if t.get("status") == status]

    def ticket_exists(self, title: str) -> bool:
        """True if an active (open/triaged/in_progress) ticket with this title exists."""
        return any(
            t["title"] == title and t.get("status") in ACTIVE_STATUSES
            for t in self.get_all_tickets()
        )

    def close_ticket(self, ticket_id: str, commit: str = "") -> None:
        self.update_ticket(ticket_id, status="closed", commit=commit)
        log.info("Closed %s (commit=%s)", ticket_id, commit or "none")

    def list_open(self) -> list[dict[str, Any]]:
        return [
            t for t in self.get_all_tickets()
            if t.get("status") in ACTIVE_STATUSES
        ]

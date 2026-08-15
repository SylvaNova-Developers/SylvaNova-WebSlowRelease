from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from .db import Database


class ProgressWriter:
    """Throttled progress + log writer used by worker processes."""

    def __init__(self, db: Database, slot_id: int, min_interval: float = 1.0):
        self.db = db
        self.slot_id = slot_id
        self.min_interval = min_interval
        self._last_write = 0.0
        self._last_status: Optional[str] = None

    def handle(self, payload: dict[str, Any]) -> None:
        now = time.monotonic()
        status = payload.get("status")
        log = payload.get("log")
        completed = bool(payload.get("completed"))
        available_count = payload.get("available_count")
        # Coerce stale BK reports when UT already has in-logic checks.
        if status == "bk" and isinstance(available_count, int) and available_count > 0:
            status = "running"
            payload = {**payload, "status": status}
        force = bool(log) or completed or status != self._last_status
        if not force and (now - self._last_write) < self.min_interval:
            return
        self._last_write = now
        self._last_status = status
        bump = bool(payload.get("current_location")) and status == "running"
        self.db.write_progress(
            self.slot_id,
            status=status,
            checked_count=payload.get("checked_count"),
            total_count=payload.get("total_count"),
            available_count=available_count,
            current_location=payload.get("current_location"),
            current_region=payload.get("current_region"),
            last_error=payload.get("error"),
            log=log,
            completed=completed,
            bump_check=bump,
        )


def check_tracker_available() -> tuple[bool, str]:
    """Return (ok, error_message) for Universal Tracker presence without importing worlds."""
    try:
        repo_worlds = Path(__file__).resolve().parent.parent / "worlds"
        if (repo_worlds / "tracker").is_dir() and (repo_worlds / "tracker" / "TrackerClient.py").exists():
            return True, ""
        if (repo_worlds / "tracker.apworld").is_file():
            return True, ""

        try:
            from Utils import user_path

            custom = Path(user_path("custom_worlds"))
            if (custom / "tracker").is_dir():
                return True, ""
            if (custom / "tracker.apworld").is_file():
                return True, ""
            # Also accept any *.apworld whose name starts with tracker
            if any(custom.glob("tracker*.apworld")):
                return True, ""
        except Exception:
            pass

        return (
            False,
            "Universal Tracker is not installed. Install the UT apworld into custom_worlds "
            "(see slowrelease_web/README.md).",
        )
    except Exception as exc:
        return False, f"Universal Tracker check failed: {exc}"

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from .models import SlotOut, utc_now_iso

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "slowrelease.db"
MAX_LOG_LINES = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slot_name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    password TEXT NOT NULL DEFAULT '',
    yaml_text TEXT NOT NULL,
    time_min REAL NOT NULL DEFAULT 10,
    time_max REAL NOT NULL DEFAULT 10,
    region_mode INTEGER NOT NULL DEFAULT 1,
    auto_goal_on_go_mode INTEGER NOT NULL DEFAULT 0,
    desired_state TEXT NOT NULL DEFAULT 'stopped',
    status TEXT NOT NULL DEFAULT 'stopped',
    checked_count INTEGER NOT NULL DEFAULT 0,
    total_count INTEGER NOT NULL DEFAULT 0,
    available_count INTEGER NOT NULL DEFAULT 0,
    current_location TEXT NOT NULL DEFAULT '',
    current_region TEXT NOT NULL DEFAULT '',
    last_check_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    pid INTEGER,
    heartbeat_at TEXT,
    restart_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slot_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id INTEGER NOT NULL,
    line TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(slot_id) REFERENCES slots(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_slot_logs_slot_id ON slot_logs(slot_id);
"""


class Database:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(_SCHEMA.replace("\t", ""))
                self._ensure_columns(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(slots)").fetchall()}
        if "auto_goal_on_go_mode" not in columns:
            conn.execute(
                "ALTER TABLE slots ADD COLUMN auto_goal_on_go_mode INTEGER NOT NULL DEFAULT 0"
            )

    def _row_to_out(self, row: sqlite3.Row) -> SlotOut:
        total = int(row["total_count"] or 0)
        checked = int(row["checked_count"] or 0)
        pct = (checked / total * 100.0) if total > 0 else 0.0
        return SlotOut(
            id=row["id"],
            name=row["name"],
            slot_name=row["slot_name"],
            host=row["host"],
            port=row["port"],
            has_password=bool(row["password"]),
            time_min=row["time_min"],
            time_max=row["time_max"],
            region_mode=bool(row["region_mode"]),
            auto_goal_on_go_mode=bool(row["auto_goal_on_go_mode"]),
            desired_state=row["desired_state"],
            status=row["status"],
            checked_count=checked,
            total_count=total,
            available_count=row["available_count"],
            current_location=row["current_location"] or "",
            current_region=row["current_region"] or "",
            last_check_at=row["last_check_at"],
            last_error=row["last_error"] or "",
            pid=row["pid"],
            heartbeat_at=row["heartbeat_at"],
            restart_count=row["restart_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            progress_pct=round(pct, 1),
        )

    def list_slots(self) -> list[SlotOut]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute("SELECT * FROM slots ORDER BY id DESC").fetchall()
        return [self._row_to_out(r) for r in rows]

    def get_slot(self, slot_id: int) -> Optional[SlotOut]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM slots WHERE id = ?", (slot_id,)).fetchone()
        return self._row_to_out(row) if row else None

    def get_slot_raw(self, slot_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM slots WHERE id = ?", (slot_id,)).fetchone()
        return dict(row) if row else None

    def create_slot(
        self,
        *,
        name: str,
        slot_name: str,
        host: str,
        port: int,
        password: str,
        yaml_text: str,
        time_min: float,
        time_max: float,
        region_mode: bool,
        auto_goal_on_go_mode: bool = False,
        desired_state: str = "stopped",
        status: str = "stopped",
    ) -> SlotOut:
        now = utc_now_iso()
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO slots (
                        name, slot_name, host, port, password, yaml_text,
                        time_min, time_max, region_mode, auto_goal_on_go_mode,
                        desired_state, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        slot_name,
                        host,
                        port,
                        password,
                        yaml_text,
                        time_min,
                        time_max,
                        1 if region_mode else 0,
                        1 if auto_goal_on_go_mode else 0,
                        desired_state,
                        status,
                        now,
                        now,
                    ),
                )
                slot_id = int(cur.lastrowid)
        slot = self.get_slot(slot_id)
        assert slot is not None
        return slot

    def update_slot(self, slot_id: int, **fields: Any) -> Optional[SlotOut]:
        if not fields:
            return self.get_slot(slot_id)
        allowed = {
            "name",
            "slot_name",
            "host",
            "port",
            "password",
            "yaml_text",
            "time_min",
            "time_max",
            "region_mode",
            "auto_goal_on_go_mode",
            "desired_state",
            "status",
            "checked_count",
            "total_count",
            "available_count",
            "current_location",
            "current_region",
            "last_check_at",
            "last_error",
            "pid",
            "heartbeat_at",
            "restart_count",
        }
        sets: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in ("region_mode", "auto_goal_on_go_mode"):
                value = 1 if value else 0
            sets.append(f"{key} = ?")
            values.append(value)
        if not sets:
            return self.get_slot(slot_id)
        sets.append("updated_at = ?")
        values.append(utc_now_iso())
        values.append(slot_id)
        with self._lock:
            with self._connect() as conn:
                conn.execute(f"UPDATE slots SET {', '.join(sets)} WHERE id = ?", values)
        return self.get_slot(slot_id)

    def delete_slot(self, slot_id: int) -> bool:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM slots WHERE id = ?", (slot_id,))
                conn.execute("DELETE FROM slot_logs WHERE slot_id = ?", (slot_id,))
                return cur.rowcount > 0

    def slots_to_resume(self) -> list[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM slots
                    WHERE desired_state = 'running' AND status != 'completed'
                    ORDER BY id ASC
                    """
                ).fetchall()
        return [dict(r) for r in rows]

    def append_log(self, slot_id: int, line: str) -> None:
        now = utc_now_iso()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO slot_logs (slot_id, line, created_at) VALUES (?, ?, ?)",
                    (slot_id, line[:2000], now),
                )
                # Cap log lines per slot
                conn.execute(
                    """
                    DELETE FROM slot_logs
                    WHERE slot_id = ? AND id NOT IN (
                        SELECT id FROM slot_logs WHERE slot_id = ? ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (slot_id, slot_id, MAX_LOG_LINES),
                )

    def get_logs(self, slot_id: int, limit: int = 200) -> list[str]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT line FROM slot_logs
                    WHERE slot_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (slot_id, limit),
                ).fetchall()
        return [r["line"] for r in reversed(rows)]

    def write_progress(
        self,
        slot_id: int,
        *,
        status: Optional[str] = None,
        checked_count: Optional[int] = None,
        total_count: Optional[int] = None,
        available_count: Optional[int] = None,
        current_location: Optional[str] = None,
        current_region: Optional[str] = None,
        last_error: Optional[str] = None,
        log: Optional[str] = None,
        completed: bool = False,
        bump_check: bool = False,
    ) -> None:
        fields: dict[str, Any] = {
            "heartbeat_at": utc_now_iso(),
        }
        if status is not None:
            fields["status"] = status
        if checked_count is not None:
            fields["checked_count"] = checked_count
        if total_count is not None:
            fields["total_count"] = total_count
        if available_count is not None:
            fields["available_count"] = available_count
        if current_location is not None:
            fields["current_location"] = current_location
        if current_region is not None:
            fields["current_region"] = current_region
        if last_error is not None:
            fields["last_error"] = last_error
        if bump_check or (current_location and status == "running"):
            fields["last_check_at"] = utc_now_iso()
        if completed:
            fields["status"] = "completed"
            fields["desired_state"] = "stopped"
            fields["pid"] = None
        self.update_slot(slot_id, **fields)
        if log:
            self.append_log(slot_id, log)

    def mark_worker_pid(self, slot_id: int, pid: Optional[int]) -> None:
        self.update_slot(slot_id, pid=pid, heartbeat_at=utc_now_iso() if pid else None)

    def increment_restart(self, slot_id: int) -> int:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE slots
                    SET restart_count = restart_count + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (utc_now_iso(), slot_id),
                )
                row = conn.execute(
                    "SELECT restart_count FROM slots WHERE id = ?", (slot_id,)
                ).fetchone()
        return int(row["restart_count"]) if row else 0

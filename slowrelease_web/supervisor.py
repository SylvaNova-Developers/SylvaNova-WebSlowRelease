from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .db import Database, DEFAULT_DB_PATH
from .models import utc_now_iso
from .progress import check_tracker_available

logger = logging.getLogger("slowrelease_web.supervisor")

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER_MODULE = "slowrelease_web.worker"
HEARTBEAT_STALE_SECONDS = 90
WATCHDOG_INTERVAL = 5.0
MAX_RESTART_BACKOFF = 60.0
BASE_RESTART_BACKOFF = 2.0

# #region agent log
_DEBUG_LOG_PATH = "/opt/cursor/logs/debug.log"


def _agent_log(hypothesis_id: str, location: str, message: str, data: dict | None = None) -> None:
    try:
        payload = {
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
            "pid": os.getpid(),
        }
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        pass
# #endregion


@dataclass
class ManagedWorker:
    slot_id: int
    process: asyncio.subprocess.Process
    started_at: float = field(default_factory=time.monotonic)
    stopping: bool = False


class Supervisor:
    def __init__(
        self,
        db: Database,
        *,
        max_workers: int = 32,
        python_executable: Optional[str] = None,
        on_change: Optional[Callable[[], None]] = None,
    ):
        self.db = db
        self.max_workers = max_workers
        self.python_executable = python_executable or sys.executable
        self.on_change = on_change
        self._workers: dict[int, ManagedWorker] = {}
        self._lock = asyncio.Lock()
        self._watch_task: Optional[asyncio.Task] = None
        self._stopped = False
        self._restart_after: dict[int, float] = {}

    @property
    def running_count(self) -> int:
        return len(self._workers)

    def _notify(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                logger.exception("on_change callback failed")

    async def start(self) -> None:
        self._stopped = False
        ok, err = check_tracker_available()
        if not ok:
            logger.warning("Universal Tracker not available: %s", err)
        # Resume desired running slots
        for raw in self.db.slots_to_resume():
            self.db.update_slot(raw["id"], status="pending", pid=None)
            await self.start_slot(raw["id"])
        self._watch_task = asyncio.create_task(self._watchdog_loop(), name="supervisor-watchdog")
        logger.info("Supervisor started (max_workers=%s)", self.max_workers)

    async def stop(self) -> None:
        self._stopped = True
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            ids = list(self._workers.keys())
        for slot_id in ids:
            await self.stop_slot(slot_id, clear_desired=False)
        logger.info("Supervisor stopped")

    async def start_slot(self, slot_id: int) -> None:
        async with self._lock:
            raw = self.db.get_slot_raw(slot_id)
            if raw is None:
                raise KeyError(f"Slot {slot_id} not found")
            if raw["status"] == "completed" and raw["desired_state"] != "running":
                return
            ok, err = check_tracker_available()
            if not ok:
                self.db.update_slot(
                    slot_id,
                    desired_state="stopped",
                    status="error",
                    last_error=err,
                    pid=None,
                )
                self.db.append_log(slot_id, err)
                self._notify()
                return
            self.db.update_slot(slot_id, desired_state="running")
            if slot_id in self._workers:
                # #region agent log
                existing = self._workers[slot_id]
                _agent_log(
                    "H1",
                    "supervisor.py:start_slot",
                    "spawn_skipped_already_running",
                    {
                        "slot_id": slot_id,
                        "existing_pid": existing.process.pid,
                        "worker_keys": list(self._workers.keys()),
                    },
                )
                # #endregion
                return
            if self.running_count >= self.max_workers:
                self.db.update_slot(slot_id, status="pending")
                self.db.append_log(slot_id, "Queued: max concurrent workers reached.")
                self._notify()
                return
            await self._spawn_locked(slot_id)

    async def stop_slot(self, slot_id: int, clear_desired: bool = True) -> None:
        async with self._lock:
            if clear_desired:
                self.db.update_slot(slot_id, desired_state="stopped")
            worker = self._workers.get(slot_id)
            if not worker:
                self.db.update_slot(slot_id, status="stopped", pid=None)
                self._notify()
                return
            worker.stopping = True
            proc = worker.process
            # #region agent log
            _agent_log(
                "H1",
                "supervisor.py:stop_slot",
                "stop_worker",
                {
                    "slot_id": slot_id,
                    "pid": proc.pid,
                    "returncode": proc.returncode,
                    "clear_desired": clear_desired,
                    "worker_keys": list(self._workers.keys()),
                },
            )
            # #endregion
            if proc.returncode is None:
                try:
                    proc.send_signal(signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
            self._workers.pop(slot_id, None)
            # #region agent log
            _agent_log(
                "H1",
                "supervisor.py:stop_slot",
                "stop_worker_done",
                {"slot_id": slot_id, "pid": proc.pid, "worker_keys": list(self._workers.keys())},
            )
            # #endregion
            self.db.update_slot(slot_id, status="stopped", pid=None)
            self.db.append_log(slot_id, "Stopped by supervisor.")
            self._notify()
            await self._start_pending_locked()

    async def restart_slot(self, slot_id: int) -> None:
        await self.stop_slot(slot_id, clear_desired=False)
        self.db.update_slot(slot_id, desired_state="running", status="pending", last_error="")
        count = self.db.increment_restart(slot_id)
        self.db.append_log(slot_id, f"Manual restart requested (restart_count={count}).")
        await self.start_slot(slot_id)

    async def _spawn_locked(self, slot_id: int) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO_ROOT), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        # Workers are headless subprocesses (not a TTY). Skip ModuleUpdate's
        # interactive prompts; deps must already be installed in this env.
        env["SKIP_REQUIREMENTS_UPDATE"] = "1"
        # Headless
        cmd = [
            self.python_executable,
            "-m",
            WORKER_MODULE,
            str(slot_id),
            str(self.db.path),
        ]
        self.db.update_slot(slot_id, status="connecting", last_error="")
        self.db.append_log(slot_id, "Spawning worker process…")
        # #region agent log
        _agent_log(
            "H1",
            "supervisor.py:_spawn_locked",
            "spawn_before",
            {
                "slot_id": slot_id,
                "worker_keys_before": list(self._workers.keys()),
                "pids_before": {
                    sid: w.process.pid for sid, w in self._workers.items()
                },
            },
        )
        # #endregion
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._workers[slot_id] = ManagedWorker(slot_id=slot_id, process=proc)
        self.db.mark_worker_pid(slot_id, proc.pid)
        # #region agent log
        _agent_log(
            "H1",
            "supervisor.py:_spawn_locked",
            "spawn_after",
            {
                "slot_id": slot_id,
                "pid": proc.pid,
                "worker_keys": list(self._workers.keys()),
                "pids": {sid: w.process.pid for sid, w in self._workers.items()},
            },
        )
        # #endregion
        asyncio.create_task(self._drain_output(slot_id, proc), name=f"worker-out-{slot_id}")
        asyncio.create_task(self._wait_process(slot_id, proc), name=f"worker-wait-{slot_id}")
        self._notify()

    async def _drain_output(self, slot_id: int, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    # Avoid duplicating structured progress; keep a short process log.
                    if len(text) < 500:
                        self.db.append_log(slot_id, text)
                        self._notify()
        except Exception:
            logger.exception("Failed draining output for slot %s", slot_id)

    async def _wait_process(self, slot_id: int, proc: asyncio.subprocess.Process) -> None:
        code = await proc.wait()
        async with self._lock:
            worker = self._workers.get(slot_id)
            stopping = worker.stopping if worker else False
            same_proc = bool(
                self._workers.get(slot_id) and self._workers[slot_id].process is proc
            )
            # #region agent log
            _agent_log(
                "H1",
                "supervisor.py:_wait_process",
                "worker_exited",
                {
                    "slot_id": slot_id,
                    "pid": proc.pid,
                    "code": code,
                    "stopping": stopping,
                    "same_proc": same_proc,
                    "current_pid": (
                        self._workers[slot_id].process.pid if slot_id in self._workers else None
                    ),
                    "worker_keys": list(self._workers.keys()),
                },
            )
            # #endregion
            if self._workers.get(slot_id) and self._workers[slot_id].process is proc:
                self._workers.pop(slot_id, None)
            raw = self.db.get_slot_raw(slot_id)
            if raw is None:
                return
            if stopping or self._stopped:
                return
            if raw["status"] == "completed" or raw["desired_state"] != "running":
                self.db.update_slot(slot_id, pid=None)
                self._notify()
                await self._start_pending_locked()
                return
            # Unexpected exit — schedule restart with backoff
            count = self.db.increment_restart(slot_id)
            backoff = min(MAX_RESTART_BACKOFF, BASE_RESTART_BACKOFF * (2 ** min(count, 5)))
            self._restart_after[slot_id] = time.monotonic() + backoff
            self.db.update_slot(
                slot_id,
                status="error" if code else "pending",
                pid=None,
                last_error=f"Worker exited with code {code}; restarting in {backoff:.0f}s",
            )
            self.db.append_log(
                slot_id,
                f"Worker exited (code={code}). Auto-restart in {backoff:.0f}s (#{count}).",
            )
            self._notify()
            await self._start_pending_locked()

    async def _start_pending_locked(self) -> None:
        if self.running_count >= self.max_workers:
            return
        now = time.monotonic()
        for raw in self.db.slots_to_resume():
            sid = raw["id"]
            if sid in self._workers:
                continue
            after = self._restart_after.get(sid, 0)
            if after > now:
                continue
            if self.running_count >= self.max_workers:
                self.db.update_slot(sid, status="pending")
                break
            self._restart_after.pop(sid, None)
            await self._spawn_locked(sid)

    async def _watchdog_loop(self) -> None:
        while not self._stopped:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL)
                await self._watchdog_tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Watchdog tick failed")

    async def _watchdog_tick(self) -> None:
        async with self._lock:
            now = time.monotonic()
            # Start any delayed restarts / pending queue
            await self._start_pending_locked()

            stale_ids: list[int] = []
            for slot_id, worker in list(self._workers.items()):
                raw = self.db.get_slot_raw(slot_id)
                if raw is None:
                    continue
                if raw["desired_state"] != "running":
                    continue
                hb = raw.get("heartbeat_at")
                if not hb:
                    # Allow grace period after spawn
                    if now - worker.started_at > HEARTBEAT_STALE_SECONDS:
                        stale_ids.append(slot_id)
                    continue
                try:
                    # Parse ISO timestamp loosely
                    from datetime import datetime

                    hb_dt = datetime.fromisoformat(hb.replace("Z", "+00:00"))
                    age = (datetime.now(hb_dt.tzinfo) - hb_dt).total_seconds()
                except Exception:
                    age = HEARTBEAT_STALE_SECONDS + 1
                if age > HEARTBEAT_STALE_SECONDS:
                    stale_ids.append(slot_id)

            for slot_id in stale_ids:
                worker = self._workers.get(slot_id)
                if not worker or worker.stopping:
                    continue
                # #region agent log
                _agent_log(
                    "H1",
                    "supervisor.py:_watchdog_tick",
                    "heartbeat_stale_kill",
                    {
                        "slot_id": slot_id,
                        "pid": worker.process.pid,
                        "worker_keys": list(self._workers.keys()),
                    },
                )
                # #endregion
                self.db.append_log(slot_id, "Heartbeat stale; killing worker for restart.")
                worker.stopping = False  # treat as crash so wait handler restarts
                try:
                    worker.process.kill()
                except ProcessLookupError:
                    pass

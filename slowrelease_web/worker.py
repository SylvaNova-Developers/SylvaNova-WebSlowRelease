from __future__ import annotations

import asyncio
import os
import signal
import sys
import tempfile
from pathlib import Path

# Ensure Archipelago repo root is on sys.path when launched as a subprocess.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from slowrelease_web.db import Database, DEFAULT_DB_PATH
from slowrelease_web.progress import ProgressWriter


def _prepare_players_dir(slot_id: int, yaml_text: str, slot_name: str) -> Path:
    base = Path(tempfile.gettempdir()) / "slowrelease_web" / f"slot_{slot_id}"
    players = base / "Players"
    players.mkdir(parents=True, exist_ok=True)
    # Clear previous yamls in this isolated folder.
    for old in players.glob("*.yaml"):
        old.unlink(missing_ok=True)
    for old in players.glob("*.yml"):
        old.unlink(missing_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in slot_name)[:40] or "player"
    path = players / f"{safe}.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    return players


def _configure_player_files_path(players_dir: Path) -> None:
    import settings

    settings.skip_autosave = True
    settings.get_settings().generator.player_files_path = (
        settings.GeneratorOptions.PlayerFilesPath(str(players_dir))
    )


async def _run_slot(slot_id: int, db_path: str) -> int:
    db = Database(db_path)
    raw = db.get_slot_raw(slot_id)
    if raw is None:
        print(f"Slot {slot_id} not found", file=sys.stderr)
        return 2

    db.mark_worker_pid(slot_id, os.getpid())
    db.update_slot(slot_id, status="connecting", last_error="")
    db.append_log(slot_id, f"Worker started (pid={os.getpid()})")

    writer = ProgressWriter(db, slot_id)
    stop_event = asyncio.Event()

    from slowrelease_web.progress import check_tracker_available

    ok, err = check_tracker_available()
    if not ok:
        db.append_log(slot_id, err)
        db.update_slot(slot_id, status="error", last_error=err, pid=None, desired_state="stopped")
        return 3

    def _on_signal(signum, frame):
        db.append_log(slot_id, f"Received signal {signum}, stopping…")
        stop_event.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    players_dir = _prepare_players_dir(slot_id, raw["yaml_text"], raw["slot_name"])
    _configure_player_files_path(players_dir)

    connect = f"{raw['host']}:{raw['port']}"
    time_min = float(raw["time_min"])
    time_max = float(raw["time_max"])
    region_mode = bool(raw["region_mode"])

    # Force headless CommonClient (no Kivy) and settings (no tkinter).
    if "--nogui" not in sys.argv:
        sys.argv.append("--nogui")
    import settings as ap_settings

    ap_settings.no_gui = True

    from slowrelease_web.models import utc_now_iso
    from worlds.slowrelease.Client import run_headless

    async def heartbeat_loop():
        while not stop_event.is_set():
            db.update_slot(slot_id, heartbeat_at=utc_now_iso())
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                continue

    heartbeat_task = asyncio.create_task(heartbeat_loop(), name="heartbeat")
    try:
        ctx = await run_headless(
            connect=connect,
            name=raw["slot_name"],
            password=raw["password"] or None,
            time_min=time_min,
            time_max=time_max,
            region_mode=region_mode,
            progress_callback=writer.handle,
            stop_event=stop_event,
            players_dir=str(players_dir),
        )
        if ctx._completed:
            db.append_log(slot_id, "Slot completed successfully.")
            db.write_progress(slot_id, status="completed", completed=True)
            return 0
        if stop_event.is_set():
            db.update_slot(slot_id, status="stopped", pid=None)
            db.append_log(slot_id, "Worker stopped.")
            return 0
        db.update_slot(slot_id, status="stopped", pid=None)
        return 0
    except Exception as exc:
        db.append_log(slot_id, f"Worker error: {exc}")
        db.update_slot(slot_id, status="error", last_error=str(exc), pid=None)
        raise
    finally:
        stop_event.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


def main(argv: list[str] | None = None) -> None:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < 1:
        print("Usage: python -m slowrelease_web.worker <slot_id> [db_path]", file=sys.stderr)
        sys.exit(2)
    slot_id = int(argv[0])
    db_path = argv[1] if len(argv) > 1 else str(DEFAULT_DB_PATH)
    try:
        code = asyncio.run(_run_slot(slot_id, db_path))
    except Exception:
        import traceback

        traceback.print_exc()
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()

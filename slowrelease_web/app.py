from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .db import Database, DEFAULT_DB_PATH
from .models import HealthOut, SlotCreate, SlotDetail, SlotOut, SlotUpdate
from .progress import check_tracker_available
from .supervisor import Supervisor

logger = logging.getLogger("slowrelease_web")

UI_DIST = Path(__file__).resolve().parent / "ui" / "dist"
DEFAULT_HOST = os.environ.get("SLOWRELEASE_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("SLOWRELEASE_PORT", "8787"))
DEFAULT_MAX_WORKERS = int(os.environ.get("SLOWRELEASE_MAX_WORKERS", "32"))
DB_PATH = Path(os.environ.get("SLOWRELEASE_DB", str(DEFAULT_DB_PATH)))


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    def publish_threadsafe(self, loop: asyncio.AbstractEventLoop, event: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(self.publish(event), loop)

    async def publish(self, event: dict[str, Any]) -> None:
        async with self._lock:
            dead: list[asyncio.Queue] = []
            for queue in self._subscribers:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    try:
                        _ = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        queue.put_nowait(event)
                    except asyncio.QueueFull:
                        dead.append(queue)
            for queue in dead:
                self._subscribers.discard(queue)


def create_app(
    db_path: Path | str = DB_PATH,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> FastAPI:
    db = Database(db_path)
    hub = EventHub()
    state: dict[str, Any] = {"supervisor": None, "loop": None}

    def on_change() -> None:
        loop = state.get("loop")
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(
                hub.publish({"type": "slots_changed"}),
                loop,
            )

    supervisor = Supervisor(db, max_workers=max_workers, on_change=on_change)
    state["supervisor"] = supervisor

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["loop"] = asyncio.get_running_loop()
        logging.basicConfig(level=logging.INFO)
        await supervisor.start()
        poll_task = asyncio.create_task(_poll_and_broadcast(db, hub), name="slot-poll")
        try:
            yield
        finally:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            await supervisor.stop()

    app = FastAPI(title="Slow Release Web Manager", version=__version__, lifespan=lifespan)

    @app.get("/api/health", response_model=HealthOut)
    async def health() -> HealthOut:
        ok, err = check_tracker_available()
        return HealthOut(
            ok=True,
            tracker_available=ok,
            tracker_error=err,
            version=__version__,
            max_workers=supervisor.max_workers,
            running_workers=supervisor.running_count,
        )

    @app.get("/api/slots", response_model=list[SlotOut])
    async def list_slots() -> list[SlotOut]:
        return db.list_slots()

    @app.post("/api/slots", response_model=SlotOut, status_code=201)
    async def create_slot(body: SlotCreate) -> SlotOut:
        time_max = body.time_max if body.time_max is not None else body.time_min
        if time_max < body.time_min:
            raise HTTPException(400, "time_max must be >= time_min")
        slot = db.create_slot(
            name=body.name,
            slot_name=body.slot_name,
            host=body.host,
            port=body.port,
            password=body.password,
            yaml_text=body.yaml_text,
            time_min=body.time_min,
            time_max=time_max,
            region_mode=body.region_mode,
            auto_goal_on_go_mode=body.auto_goal_on_go_mode,
            desired_state="running" if body.start else "stopped",
            status="pending" if body.start else "stopped",
        )
        db.append_log(slot.id, "Slot created.")
        if body.start:
            await supervisor.start_slot(slot.id)
        else:
            on_change()
        refreshed = db.get_slot(slot.id)
        assert refreshed is not None
        return refreshed

    @app.get("/api/slots/{slot_id}", response_model=SlotDetail)
    async def get_slot(slot_id: int) -> SlotDetail:
        raw = db.get_slot_raw(slot_id)
        if raw is None:
            raise HTTPException(404, "Slot not found")
        out = db.get_slot(slot_id)
        assert out is not None
        return SlotDetail(
            **out.model_dump(),
            yaml_text=raw["yaml_text"],
            logs=db.get_logs(slot_id),
        )

    @app.patch("/api/slots/{slot_id}", response_model=SlotOut)
    async def patch_slot(slot_id: int, body: SlotUpdate) -> SlotOut:
        if db.get_slot(slot_id) is None:
            raise HTTPException(404, "Slot not found")
        fields = body.model_dump(exclude_unset=True)
        if "time_min" in fields or "time_max" in fields:
            raw = db.get_slot_raw(slot_id)
            assert raw is not None
            tmin = fields.get("time_min", raw["time_min"])
            tmax = fields.get("time_max", raw["time_max"])
            if tmax < tmin:
                raise HTTPException(400, "time_max must be >= time_min")
        updated = db.update_slot(slot_id, **fields)
        assert updated is not None
        # Timing / region / auto-goal are applied live by the worker settings sync.
        # No restart required for those fields.
        on_change()
        if any(k in fields for k in ("time_min", "time_max")):
            db.append_log(
                slot_id,
                f"Timing set to {updated.time_min:g}–{updated.time_max:g}s "
                "(applies live within a couple seconds if running).",
            )
        return db.get_slot(slot_id)  # type: ignore

    @app.post("/api/slots/{slot_id}/start", response_model=SlotOut)
    async def start_slot(slot_id: int) -> SlotOut:
        if db.get_slot(slot_id) is None:
            raise HTTPException(404, "Slot not found")
        raw = db.get_slot_raw(slot_id)
        assert raw is not None
        if raw["status"] == "completed":
            # Allow re-run after completion by resetting progress flags.
            db.update_slot(
                slot_id,
                status="pending",
                last_error="",
                current_location="",
            )
        await supervisor.start_slot(slot_id)
        return db.get_slot(slot_id)  # type: ignore

    @app.post("/api/slots/{slot_id}/stop", response_model=SlotOut)
    async def stop_slot(slot_id: int) -> SlotOut:
        if db.get_slot(slot_id) is None:
            raise HTTPException(404, "Slot not found")
        await supervisor.stop_slot(slot_id, clear_desired=True)
        return db.get_slot(slot_id)  # type: ignore

    @app.post("/api/slots/{slot_id}/restart", response_model=SlotOut)
    async def restart_slot(slot_id: int) -> SlotOut:
        if db.get_slot(slot_id) is None:
            raise HTTPException(404, "Slot not found")
        await supervisor.restart_slot(slot_id)
        return db.get_slot(slot_id)  # type: ignore

    @app.delete("/api/slots/{slot_id}", status_code=204)
    async def delete_slot(slot_id: int) -> None:
        if db.get_slot(slot_id) is None:
            raise HTTPException(404, "Slot not found")
        await supervisor.stop_slot(slot_id, clear_desired=True)
        db.delete_slot(slot_id)
        on_change()

    @app.websocket("/api/events")
    async def events(ws: WebSocket) -> None:
        await ws.accept()
        queue = await hub.subscribe()
        try:
            await ws.send_json({"type": "hello", "slots": [s.model_dump() for s in db.list_slots()]})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                    if event.get("type") == "slots_changed":
                        event = {
                            "type": "slots",
                            "slots": [s.model_dump() for s in db.list_slots()],
                        }
                    await ws.send_json(event)
                except asyncio.TimeoutError:
                    await ws.send_json({"type": "ping"})
        except WebSocketDisconnect:
            pass
        finally:
            await hub.unsubscribe(queue)

    if UI_DIST.is_dir():
        assets = UI_DIST / "assets"
        index_html = UI_DIST / "index.html"
        _warn_mismatched_ui_dist(index_html, assets)
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(index_html)

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str) -> FileResponse:
            candidate = UI_DIST / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_html)

    return app


def _warn_mismatched_ui_dist(index_html: Path, assets: Path) -> None:
    """Log a clear error when index.html references hashed assets that are missing.

    A partial/stale Vite build leaves the SPA as a white screen (JS 404).
    """
    if not index_html.is_file() or not assets.is_dir():
        return
    try:
        html = index_html.read_text(encoding="utf-8")
    except OSError:
        return
    missing: list[str] = []
    for match in re.finditer(r"""(?:src|href)=["'](/assets/[^"']+)["']""", html):
        rel = match.group(1).removeprefix("/assets/")
        if not (assets / rel).is_file():
            missing.append(match.group(1))
    if missing:
        logger.error(
            "UI dist is mismatched (white screen likely). Missing %s. "
            "Rebuild with: cd slowrelease_web/ui && npm run build",
            ", ".join(missing),
        )


async def _poll_and_broadcast(db: Database, hub: EventHub) -> None:
    """Periodic snapshot so UI stays fresh even if a notify is missed."""
    last: Optional[str] = None
    while True:
        await asyncio.sleep(2)
        slots = db.list_slots()
        fingerprint = "|".join(
            f"{s.id}:{s.status}:{s.checked_count}:{s.heartbeat_at}:{s.current_location}"
            for s in slots
        )
        if fingerprint != last:
            last = fingerprint
            await hub.publish({"type": "slots", "slots": [s.model_dump() for s in slots]})


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "slowrelease_web.app:app",
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()

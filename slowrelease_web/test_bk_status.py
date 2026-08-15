"""Regression tests for sticky BK status when checks become in-logic."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from slowrelease_web.db import Database
from slowrelease_web.progress import ProgressWriter


class ProgressWriterBkCoercionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmpdir.name) / "test.db")
        created = self.db.create_slot(
            name="t",
            slot_name="Player",
            host="127.0.0.1",
            port=38281,
            password="",
            yaml_text="name: Player\ngame: APQuest\n",
            time_min=1,
            time_max=1,
            region_mode=True,
            desired_state="running",
        )
        self.slot_id = created.id
        self.writer = ProgressWriter(self.db, self.slot_id, min_interval=10.0)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_coerces_bk_to_running_when_available(self) -> None:
        self.writer.handle({"status": "bk", "available_count": 0, "checked_count": 1, "total_count": 2})
        row = self.db.get_slot_raw(self.slot_id)
        self.assertEqual(row["status"], "bk")

        self.writer.handle({"status": "bk", "available_count": 3, "checked_count": 1, "total_count": 2})
        row = self.db.get_slot_raw(self.slot_id)
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["available_count"], 3)


class LeaveBkOrderingTests(unittest.TestCase):
    def test_leave_bk_emits_running_not_bk(self) -> None:
        from worlds.slowrelease.Client import SlowReleaseContext

        emitted: list[dict] = []

        ctx = SlowReleaseContext.__new__(SlowReleaseContext)
        ctx._in_bk = True
        ctx._completed = False
        ctx._stop_requested = False
        ctx._current_location_name = ""
        ctx._current_region_name = ""
        ctx.checked_locations = set()
        ctx.missing_locations = {1, 2}
        ctx.server = MagicMock()
        ctx.server.socket = MagicMock()
        ctx.server.socket.closed = False
        core = MagicMock()
        core.locations_available = [10, 20]
        ctx.tracker_core = core
        ctx.progress_callback = emitted.append

        ctx._leave_bk()

        self.assertFalse(ctx._in_bk)
        self.assertTrue(emitted)
        # First progress payload must already be running (not sticky bk + Out of BK log).
        self.assertEqual(emitted[0].get("status"), "running")
        self.assertIn("Out of BK", emitted[0].get("log", ""))


class ReceivedItemsLeavesBkWhileWaitingTests(unittest.IsolatedAsyncioTestCase):
    async def test_received_items_clears_bk_during_wait(self) -> None:
        from worlds.slowrelease.Client import SlowReleaseContext

        emitted: list[dict] = []
        ctx = SlowReleaseContext.__new__(SlowReleaseContext)
        ctx._in_bk = True
        ctx._completed = False
        ctx._stop_requested = False
        ctx._current_location_name = ""
        ctx._current_region_name = ""
        ctx._logic_wakeup = asyncio.Event()
        ctx.checked_locations = set()
        ctx.missing_locations = {1}
        ctx.server = MagicMock()
        ctx.server.socket = MagicMock()
        ctx.server.socket.closed = False
        core = MagicMock()
        core.locations_available = []
        ctx.tracker_core = core
        ctx.progress_callback = emitted.append
        ctx.tags = ["SlowRelease"]

        def update_tracker():
            core.locations_available = [42]
            return MagicMock(state=MagicMock())

        ctx.updateTracker = update_tracker  # type: ignore[method-assign]

        # Simulate being parked in BK wait, then ReceivedItems arrives.
        wait_task = asyncio.create_task(ctx._ensure_logic_wakeup().wait())
        await asyncio.sleep(0)
        ctx.on_package("ReceivedItems", {"index": 1, "items": []})
        await asyncio.wait_for(wait_task, timeout=1.0)

        self.assertFalse(ctx._in_bk)
        statuses = [p.get("status") for p in emitted]
        self.assertIn("running", statuses)
        self.assertTrue(any("Out of BK" in (p.get("log") or "") for p in emitted))


if __name__ == "__main__":
    unittest.main()

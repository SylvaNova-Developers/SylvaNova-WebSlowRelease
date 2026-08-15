"""Smoke test for Slow Release Web Manager (no live Archipelago room required)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SAMPLE_YAML = """
name: SmokePlayer
description: Smoke test yaml
game: Timespinner
requires:
  version: 0.4.0
Timespinner: {}
""".strip()


def main() -> None:
    try:
        import httpx  # noqa: F401
    except ImportError:
        import subprocess

        subprocess.check_call([sys.executable, "-m", "pip", "install", "httpx"])

    from fastapi.testclient import TestClient

    from slowrelease_web.app import create_app

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        app = create_app(db_path=db_path, max_workers=2)

        with TestClient(app) as client:
            health = client.get("/api/health")
            assert health.status_code == 200, health.text
            body = health.json()
            assert "tracker_available" in body
            print("health ok; tracker_available=", body["tracker_available"])

            created = client.post(
                "/api/slots",
                json={
                    "name": "Smoke Slot",
                    "slot_name": "SmokePlayer",
                    "host": "127.0.0.1",
                    "port": 38281,
                    "password": "",
                    "yaml_text": SAMPLE_YAML,
                    "time_min": 5,
                    "time_max": 5,
                    "region_mode": True,
                    "auto_goal_on_go_mode": True,
                    "start": False,
                },
            )
            assert created.status_code == 201, created.text
            slot = created.json()
            slot_id = slot["id"]
            assert slot["status"] == "stopped"
            assert slot["auto_goal_on_go_mode"] is True
            print("created slot", slot_id)

            listed = client.get("/api/slots")
            assert listed.status_code == 200
            assert any(s["id"] == slot_id for s in listed.json())

            detail = client.get(f"/api/slots/{slot_id}")
            assert detail.status_code == 200
            assert "Timespinner" in detail.json()["yaml_text"]

            patched = client.patch(
                f"/api/slots/{slot_id}",
                json={"time_min": 8, "time_max": 12, "auto_goal_on_go_mode": False},
            )
            assert patched.status_code == 200, patched.text
            assert patched.json()["time_min"] == 8
            assert patched.json()["auto_goal_on_go_mode"] is False
            print("patched ok")

            assert body.get("tracker_available"), "Universal Tracker should be bundled under worlds/tracker"
            started = client.post(f"/api/slots/{slot_id}/start")
            assert started.status_code == 200, started.text
            print("start requested; status=", started.json()["status"])
            # Give the worker a moment to spawn (may error connecting without a live room).
            import time

            time.sleep(2)
            stopped = client.post(f"/api/slots/{slot_id}/stop")
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["desired_state"] == "stopped"
            print("stopped ok")

            deleted = client.delete(f"/api/slots/{slot_id}")
            assert deleted.status_code == 204, deleted.text
            print("deleted ok")

            empty = client.get("/api/slots")
            assert empty.json() == []
            print("SMOKE PASS")


if __name__ == "__main__":
    main()

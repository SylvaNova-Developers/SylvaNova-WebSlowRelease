# Slow Release Web Manager

Standalone web app that runs many [Slow Release](../worlds/slowrelease/) slots in parallel against one or more Archipelago rooms. Paste a player YAML and connection details; workers keep checking in-logic locations until the slot is complete.

## Features

- Add as many slots as you want (same room or different rooms)
- Live progress dashboard (WebSocket updates)
- One isolated worker process per slot
- SQLite persistence with resume after manager restart
- Auto-reconnect (via Archipelago CommonClient) and auto-restart on worker crash
- Heartbeat watchdog for stuck processes

## Docker (recommended)

```bash
# From repo root — build locally
./slowrelease_web/scripts/docker-build.sh

# Or pull a published release image
docker pull ghcr.io/chouticly/sylvanova-webslowrelease:0.2.0

# Run
docker run --rm -p 8787:8787 \
  -v slowrelease-data:/data \
  -v "$PWD/custom_worlds:/data/custom_worlds" \
  ghcr.io/chouticly/sylvanova-webslowrelease:0.2.0

# Compose
cd slowrelease_web
docker compose up -d
```

Open http://localhost:8787

| Variable | Default | Meaning |
|----------|---------|---------|
| `SLOWRELEASE_HOST` | `0.0.0.0` (in image) | Bind address |
| `SLOWRELEASE_PORT` | `8787` | HTTP port |
| `SLOWRELEASE_DB` | `/data/slowrelease.db` | SQLite path |
| `SLOWRELEASE_MAX_WORKERS` | `32` | Max concurrent worker processes |
| `SLOWRELEASE_CUSTOM_WORLDS` | `/data/custom_worlds` | Extra `.apworld` drop folder (also where autodownloaded worlds are stored) |
| `SLOWRELEASE_APWORLD_AUTODOWNLOAD` | `1` | Fetch missing game apworlds from the SylvaNova index (`0` to disable) |
| `SLOWRELEASE_APWORLD_INDEX_URL` | GitHub archive of `chouticly/SylvaNova-archipelago-index` | APWM index tarball URL |
| `SLOWRELEASE_APWORLD_INDEX_PATH` | unset | Local index checkout (tests / airgap); skips the tarball download |

Published images and GitHub Releases are created from `slowrelease-v*` tags (see `.github/workflows/slowrelease-docker.yml`).

## Requirements (from source)

- Python 3.11–3.13 with this Archipelago tree
- Node.js 20+ (only if you need to rebuild the UI)

**Universal Tracker is bundled** under [`worlds/tracker/`](../worlds/tracker/) (vendored from [Tracker_v0.3.3](https://github.com/FarisTheAncient/Archipelago/releases/tag/Tracker_v0.3.3)). No separate apworld install is required for the web manager.

## Setup (from source)

```bash
# From the Archipelago repo root
pip install -r requirements.txt
pip install -r slowrelease_web/requirements.txt
# Optional UI rebuild:
# cd slowrelease_web/ui && npm install && npm run build && cd ../..
```

## Run (from source)

```bash
# Default: http://127.0.0.1:8787
python -m slowrelease_web
```

Environment variables:

| Variable | Default | Meaning |
|----------|---------|---------|
| `SLOWRELEASE_HOST` | `127.0.0.1` | Bind address (keep localhost unless you put a reverse proxy in front) |
| `SLOWRELEASE_PORT` | `8787` | HTTP port |
| `SLOWRELEASE_DB` | `slowrelease_web/data/slowrelease.db` | SQLite path |
| `SLOWRELEASE_MAX_WORKERS` | `32` | Max concurrent worker processes |
| `SLOWRELEASE_APWORLD_AUTODOWNLOAD` | `1` | Fetch missing game apworlds from the SylvaNova index (`0` to disable) |
| `SLOWRELEASE_APWORLD_INDEX_URL` | GitHub archive of `chouticly/SylvaNova-archipelago-index` | APWM index tarball URL |
| `SLOWRELEASE_APWORLD_INDEX_PATH` | unset | Local index checkout (tests / airgap); skips the tarball download |

## Using the UI

1. Open the app in a browser.
2. **Add slot**: paste or upload the slot’s player YAML, set host/port, slot name, password, timing, region mode.
3. Start the slot — a worker connects and slow-releases checks.
4. Watch status (`connecting` → `running` / `In BK` → `completed`), progress bar, and logs.
5. Stop / restart / delete as needed.

Completion is when the server reports no remaining missing locations; the worker sends `CLIENT_GOAL` and stops.

## API (brief)

- `GET /api/health` — tracker availability + worker counts
- `GET/POST /api/slots` — list / create
- `GET/PATCH/DELETE /api/slots/{id}`
- `POST /api/slots/{id}/start|stop|restart`
- `WS /api/events` — live slot snapshots

## Smoke test

```bash
python -m slowrelease_web.smoke_test
```

Exercises DB + HTTP CRUD without requiring a live Archipelago room (workers that actually connect still need Universal Tracker + a room).

## Notes

- Single-user / self-hosted; no login. Do not expose publicly without a reverse proxy and access control.
- Each worker writes YAML into an isolated temp `Players` folder for Universal Tracker generation.
- Custom games (e.g. Keep Talking and Nobody Explodes) are autodownloaded from the [SylvaNova APWM index](https://github.com/chouticly/SylvaNova-archipelago-index) into `custom_worlds` before Universal Tracker starts. The latest index version is used. You can still drop `.apworld` files into that folder manually.
- The classic Launcher Slow Release Client remains available unchanged.

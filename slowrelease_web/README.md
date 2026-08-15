# Slow Release Web Manager

Standalone web app that runs many [Slow Release](../worlds/slowrelease/) slots in parallel against one or more Archipelago rooms. Paste a player YAML and connection details; workers keep checking in-logic locations until the slot is complete.

## Features

- Add as many slots as you want (same room or different rooms)
- Live progress dashboard (WebSocket updates)
- One isolated worker process per slot
- SQLite persistence with resume after manager restart
- Auto-reconnect (via Archipelago CommonClient) and auto-restart on worker crash
- Heartbeat watchdog for stuck processes

## Requirements

- Python 3.11–3.13 with this Archipelago tree
- **Universal Tracker** apworld installed (`custom_worlds` / user worlds) so `worlds.tracker` imports
- Node.js 20+ (to build the UI once)

## Setup

```bash
# From the Archipelago repo root
pip install -r slowrelease_web/requirements.txt
cd slowrelease_web/ui && npm install && npm run build && cd ../..
```

Install [Universal Tracker](https://github.com/FarisTheAncient/Archipelago/releases) into your Archipelago `custom_worlds` folder (same as the desktop Slow Release client).

## Run

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
- The classic Launcher Slow Release Client remains available unchanged.

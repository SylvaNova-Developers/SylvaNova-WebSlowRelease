# AGENTS.md

## Cursor Cloud specific instructions

The product in this repo for cloud-agent work is the **Slow Release Web Manager** (`slowrelease_web/`), a FastAPI + React dashboard that runs headless Slow Release workers against Archipelago rooms. Canonical setup/run docs: `slowrelease_web/README.md`. Do not treat Archipelago `WebHost.py` or the Kivy Slow Release Client GUI as the thing to run unless a task explicitly asks for them.

### Python / Node environment

- Use the dedicated virtualenv at `$HOME/apvenv`. Run Python with `"$HOME/apvenv/bin/python"`. Do not use system `python3`/`pip` (PEP 668 externally-managed).
- Universal Tracker is **bundled** at `worlds/tracker/` (see `slowrelease_web/README.md`). Do not download `tracker.apworld` into `custom_worlds/` for this app.
- Frontend toolchain lives in `slowrelease_web/ui/` (`package-lock.json` → npm). `npm ci` is run by the startup update script. A production UI build is already committed under `slowrelease_web/ui/dist/`; `python -m slowrelease_web` serves that dist. Rebuild with `npm run build` only when UI source changed.

### Run the web UI

From the repo root:

```bash
"$HOME/apvenv/bin/python" -m slowrelease_web
```

Binds `http://127.0.0.1:8787` by default (`SLOWRELEASE_HOST` / `SLOWRELEASE_PORT` / `SLOWRELEASE_DB` — see the README table). Health: `GET /api/health` (`tracker_available` should be true).

For UI HMR during frontend work, run Vite **in addition** (it proxies `/api` to `:8787`):

```bash
# terminal 1: python -m slowrelease_web  (must already be on 8787)
cd slowrelease_web/ui && npm run dev
```

Vite defaults to `http://127.0.0.1:5173`. Do not use `npm run build` as the everyday run command.

Workers that actually connect still need a live Archipelago room (`MultiServer.py <archive.zip>` or a remote room). Generating a local room: put player YAMLs in `Players/`, then `"$HOME/apvenv/bin/python" Generate.py --player_files_path Players` and `"$HOME/apvenv/bin/python" MultiServer.py output/<archive>.zip`.

### Lint / test

- UI lint: `cd slowrelease_web/ui && npm run lint` (oxlint).
- App smoke test (DB + HTTP CRUD, no live room required): `"$HOME/apvenv/bin/python" -m slowrelease_web.smoke_test`
- Scope Python lint to `slowrelease_web/` if you lint at all; repo-wide ruff/flake8 against Archipelago core is out of scope and noisy.

### SQLite / data

The manager DB defaults to `slowrelease_web/data/slowrelease.db` (gitignored). Stopping the uvicorn process does not always stop worker children; if a slot looks stuck after a restart, check leftover `slowrelease_web.worker` processes.

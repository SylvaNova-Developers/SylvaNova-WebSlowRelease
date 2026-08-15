# AGENTS.md

## Cursor Cloud specific instructions

Archipelago is a multiworld multi-game randomizer written in Python. See `README.md` and
`docs/running from source.md` for the canonical developer overview, and `docs/tests.md` for testing.

### Python environment (important)

- Use the dedicated virtualenv at `$HOME/apvenv`. Run project commands with `"$HOME/apvenv/bin/python"`
  (or `source "$HOME/apvenv/bin/activate"` first). Do NOT use the system `python3`/`pip`.
- The system interpreter is marked externally-managed (PEP 668), so `pip install` there fails. All Python
  dependencies (core `requirements.txt`, every `worlds/*/requirements.txt`, `WebHostLib/requirements.txt`,
  `ci-requirements.txt`, plus `ruff`) are installed inside the venv by the startup update script.
- The optional `_speedups` C extension is compiled just-in-time via `pyximport` on first import of
  `NetUtils` (build tooling `build-essential`/`python3-dev` is present in the image). If it fails you only
  get a warning and a slower pure-Python fallback; it is not fatal.

### Services / entry points (all run from repo root, via the venv python)

- `WebHost.py` — the archipelago.gg website (Flask + waitress, plus background room/generation workers).
  Defaults to port 80 and does a public-IP lookup. For local dev a gitignored `config.yaml` overrides this
  (`PORT: 8080`, `HOST_ADDRESS: "127.0.0.1"`); create one if missing. It spawns several worker processes and
  takes ~30-60s to start; then `http://127.0.0.1:8080/` serves the site. Generating a seed via the web UI
  works; hosting a room currently errors (see known issues).
- `Generate.py` — CLI multiworld generator. Reads player YAMLs from the `Players/` folder (configurable in
  `host.yaml`) and writes an archive to `output/`. Example: `python Generate.py --player_files_path Players --seed 1`.
- `MultiServer.py <archive.zip>` — hosts a generated multiworld over websockets (default port 38281).
- `Launcher.py` and the various `*Client.py` GUIs use Kivy and need a display; they are not needed for
  headless backend/web work. `python Launcher.py --update_settings` (re)creates `host.yaml`.

### Lint / test / run commands

- Tests: `"$HOME/apvenv/bin/python" -m pytest -n auto` (full suite ~3.5 min on 4 cores). CI equivalent lives
  in `.github/workflows/unittests.yml`.
- Lint: the repo ships `ruff.toml`, so `ruff check .` works, but note CI (`analyze-modified-files.yml`) runs
  `flake8` only on files changed in a PR. `ruff check .` over the whole tree reports a very large number of
  pre-existing findings; scope lint to files you actually change.
- `host.yaml` is auto-created on first run and is needed by some tests; it already exists in the image.

### Known pre-existing failures (NOT environment problems)

- The committed world `worlds/slotlock` is broken. It causes ~1021 pytest subtest failures (all
  `game_name='SlotLock'`), the `test/webhost/test_sitemap.py::TestSitemap::test_sitemap_links` failure
  (`'WebWorld' object has no attribute 'tutorials'`), and a `KeyError: 'SlotLock'` in
  `MultiServer._init_game_data` when WebHost tries to host a room. The rest of the suite (3528 tests +
  ~223k subtests) passes. Do not treat these as setup breakage.

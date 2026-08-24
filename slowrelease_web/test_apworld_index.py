"""Unit tests for SylvaNova apworld autodownload."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import httpx

from slowrelease_web.apworld_index import (
    ApworldIndexSettings,
    choose_version,
    ensure_apworld_for_yaml,
    game_from_yaml,
    parse_index_dir,
    parse_world_toml,
    resolve_download_url,
)


KTANE_TOML = """
name = "Keep Talking and Nobody Explodes"
home = "https://example.invalid/ktane"

[versions]
"0.2.0" = { url = "https://example.invalid/ktane-0.2.0.apworld" }
"0.3.0+c" = { url = "https://example.invalid/ktane-0.3.0c.apworld" }
"""

ANIMAL_TOML = """
name = "ANIMAL WELL"
default_url = "https://example.invalid/releases/{{version}}/animal_well.apworld"

[versions]
"0.5.2" = {}
"0.5.4" = {}
"""

LOCAL_TOML = """
name = "Local Puzzle"
[versions]
"1.0.0" = { local = "../apworlds/local_puzzle-1.0.0.apworld" }
"""

CORE_TOML = """
name = "Timespinner"
supported = true
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_index(root: Path, worlds: dict[str, str], lock: str = "") -> None:
    (root / "index").mkdir(parents=True, exist_ok=True)
    (root / "index.toml").write_text("index_dir = \"index\"\n", encoding="utf-8")
    if lock:
        (root / "index.lock").write_text(lock, encoding="utf-8")
    for apworld_id, body in worlds.items():
        (root / "index" / f"{apworld_id}.toml").write_text(body.strip() + "\n", encoding="utf-8")


def _settings(tmp: Path, index_root: Path) -> ApworldIndexSettings:
    custom = tmp / "custom_worlds"
    worlds = tmp / "worlds"
    custom.mkdir(parents=True, exist_ok=True)
    worlds.mkdir(parents=True, exist_ok=True)
    return ApworldIndexSettings(
        autodownload=True,
        index_path=index_root,
        custom_worlds_dir=custom,
        worlds_dir=worlds,
        raw_base="https://raw.githubusercontent.com/example/index/main",
    )


class GameFromYamlTests(unittest.TestCase):
    def test_string_game(self) -> None:
        yaml_text = "name: Player\ngame: Keep Talking and Nobody Explodes\n"
        self.assertEqual(game_from_yaml(yaml_text), "Keep Talking and Nobody Explodes")

    def test_list_game(self) -> None:
        yaml_text = "game:\n  - Super Metroid\n  - ALttP\n"
        self.assertEqual(game_from_yaml(yaml_text), "Super Metroid")

    def test_weighted_game(self) -> None:
        yaml_text = "game:\n  Factorio: 1\n"
        self.assertEqual(game_from_yaml(yaml_text), "Factorio")

    def test_missing_game(self) -> None:
        self.assertEqual(game_from_yaml("name: Player\n"), "")


class IndexParseTests(unittest.TestCase):
    def test_lookup_ktane_and_latest_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_index(
                root,
                {"ktane": KTANE_TOML},
                lock='[ktane]\n"0.3.0+c" = "abc123"\n',
            )
            catalog = parse_index_dir(root)
            world = catalog.lookup("keep talking and nobody explodes")
            self.assertIsNotNone(world)
            assert world is not None
            self.assertEqual(world.apworld_id, "ktane")
            self.assertEqual(choose_version(world), "0.3.0+c")
            self.assertEqual(world.checksums["0.3.0+c"], "abc123")

    def test_default_url_template(self) -> None:
        world = parse_world_toml("animal_well", ANIMAL_TOML, {})
        assert world is not None
        self.assertEqual(choose_version(world), "0.5.4")
        url = resolve_download_url(world, "0.5.4", "https://unused.example")
        self.assertEqual(url, "https://example.invalid/releases/0.5.4/animal_well.apworld")

    def test_local_url_rewrite(self) -> None:
        world = parse_world_toml("local_puzzle", LOCAL_TOML, {})
        assert world is not None
        url = resolve_download_url(world, "1.0.0", "https://raw.example/main")
        self.assertEqual(url, "https://raw.example/main/apworlds/local_puzzle-1.0.0.apworld")


class EnsureApworldTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(Path(tmp), Path(tmp) / "index")
            settings.autodownload = False
            result = await ensure_apworld_for_yaml("game: KTANE\n", settings=settings)
            self.assertEqual(result.outcome, "disabled")
            self.assertTrue(result.ok)

    async def test_skip_when_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            index_root = tmp_path / "index_src"
            _write_index(index_root, {"ktane": KTANE_TOML})
            settings = _settings(tmp_path, index_root)
            existing = settings.custom_worlds_dir / "ktane.apworld"
            existing.write_bytes(b"already-here")
            result = await ensure_apworld_for_yaml(
                "game: Keep Talking and Nobody Explodes\n",
                settings=settings,
            )
            self.assertEqual(result.outcome, "already_present")
            self.assertEqual(existing.read_bytes(), b"already-here")

    async def test_skip_bundled_world(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            index_root = tmp_path / "index_src"
            _write_index(index_root, {"ktane": KTANE_TOML})
            settings = _settings(tmp_path, index_root)
            bundled = settings.worlds_dir / "ktane"
            bundled.mkdir()
            (bundled / "__init__.py").write_text("# bundled\n", encoding="utf-8")
            result = await ensure_apworld_for_yaml(
                "game: Keep Talking and Nobody Explodes\n",
                settings=settings,
            )
            self.assertEqual(result.outcome, "already_present")
            self.assertFalse((settings.custom_worlds_dir / "ktane.apworld").exists())

    async def test_core_world_not_downloaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            index_root = tmp_path / "index_src"
            _write_index(index_root, {"timespinner": CORE_TOML})
            settings = _settings(tmp_path, index_root)
            result = await ensure_apworld_for_yaml("game: Timespinner\n", settings=settings)
            self.assertEqual(result.outcome, "core")
            self.assertTrue(result.ok)

    async def test_bundled_game_skips_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            settings = _settings(tmp_path, tmp_path / "missing_index")
            bundled = settings.worlds_dir / "timespinner"
            bundled.mkdir()
            (bundled / "__init__.py").write_text("# core\n", encoding="utf-8")
            result = await ensure_apworld_for_yaml("game: Timespinner\n", settings=settings)
            self.assertEqual(result.outcome, "core")
            self.assertTrue(result.ok)

    async def test_not_in_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            index_root = tmp_path / "index_src"
            _write_index(index_root, {"ktane": KTANE_TOML})
            settings = _settings(tmp_path, index_root)
            result = await ensure_apworld_for_yaml("game: Unknown Game\n", settings=settings)
            self.assertEqual(result.outcome, "not_in_index")
            self.assertTrue(result.ok)

    async def test_downloads_and_verifies_checksum(self) -> None:
        payload = b"ktane-apworld-bytes"
        digest = _sha256(payload)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            index_root = tmp_path / "index_src"
            _write_index(
                index_root,
                {"ktane": KTANE_TOML},
                lock=f'[ktane]\n"0.3.0+c" = "{digest}"\n',
            )
            settings = _settings(tmp_path, index_root)

            def handler(request: httpx.Request) -> httpx.Response:
                if str(request.url).endswith("ktane-0.3.0c.apworld"):
                    return httpx.Response(200, content=payload)
                return httpx.Response(404, text="missing")

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                result = await ensure_apworld_for_yaml(
                    "game: Keep Talking and Nobody Explodes\n",
                    settings=settings,
                    client=client,
                )
            self.assertEqual(result.outcome, "downloaded", result.message)
            dest = settings.custom_worlds_dir / "ktane.apworld"
            self.assertTrue(dest.is_file())
            self.assertEqual(dest.read_bytes(), payload)

    async def test_checksum_mismatch_rejects(self) -> None:
        payload = b"tampered"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            index_root = tmp_path / "index_src"
            _write_index(
                index_root,
                {"ktane": KTANE_TOML},
                lock='[ktane]\n"0.3.0+c" = "deadbeef"\n',
            )
            settings = _settings(tmp_path, index_root)

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, content=payload)

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                result = await ensure_apworld_for_yaml(
                    "game: Keep Talking and Nobody Explodes\n",
                    settings=settings,
                    client=client,
                )
            self.assertEqual(result.outcome, "failed")
            self.assertFalse(result.ok)
            self.assertIn("Checksum mismatch", result.message)
            self.assertFalse((settings.custom_worlds_dir / "ktane.apworld").exists())

    async def test_local_origin_copies_from_index_checkout(self) -> None:
        payload = b"local-apworld"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            index_root = tmp_path / "index_src"
            _write_index(
                index_root,
                {"local_puzzle": LOCAL_TOML},
                lock=f'[local_puzzle]\n"1.0.0" = "{_sha256(payload)}"\n',
            )
            apworlds = index_root / "apworlds"
            apworlds.mkdir()
            (apworlds / "local_puzzle-1.0.0.apworld").write_bytes(payload)
            settings = _settings(tmp_path, index_root)
            result = await ensure_apworld_for_yaml("game: Local Puzzle\n", settings=settings)
            self.assertEqual(result.outcome, "downloaded", result.message)
            dest = settings.custom_worlds_dir / "local_puzzle.apworld"
            self.assertEqual(dest.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()

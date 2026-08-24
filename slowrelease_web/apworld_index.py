"""Fetch missing game apworlds from the SylvaNova APWM index into custom_worlds."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("slowrelease_web.apworld_index")

DEFAULT_INDEX_ARCHIVE_URL = (
    "https://github.com/SylvaNova-Developers/SylvaNova-Archipelago-Index/archive/refs/heads/main.tar.gz"
)
DEFAULT_INDEX_RAW_BASE = (
    "https://raw.githubusercontent.com/SylvaNova-Developers/SylvaNova-Archipelago-Index/main"
)
CACHE_TTL_SECONDS = 6 * 60 * 60
DOWNLOAD_TIMEOUT = 120.0
INDEX_TIMEOUT = 60.0

EnsureOutcome = Literal[
    "disabled",
    "no_game",
    "not_in_index",
    "core",
    "already_present",
    "downloaded",
    "failed",
]


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def autodownload_enabled() -> bool:
    return _env_flag("SLOWRELEASE_APWORLD_AUTODOWNLOAD", True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_custom_worlds_dir() -> Path:
    try:
        from Utils import local_path, user_path

        if user_path() != local_path():
            return Path(user_path("worlds"))
        return Path(user_path("custom_worlds"))
    except Exception:
        return Path(__file__).resolve().parent.parent / "custom_worlds"


def default_worlds_dir() -> Path:
    try:
        from Utils import local_path

        return Path(local_path("worlds"))
    except Exception:
        return Path(__file__).resolve().parent.parent / "worlds"


@dataclass(frozen=True)
class VersionSource:
    kind: Literal["url", "local"]
    spec: str


@dataclass(frozen=True)
class IndexWorld:
    apworld_id: str
    name: str
    display_name: str
    supported: bool
    disabled: bool
    default_url: str
    default_version: str
    versions: dict[str, VersionSource]
    checksums: dict[str, str]


@dataclass
class IndexCatalog:
    worlds: list[IndexWorld]
    fetched_at: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        self._by_name: dict[str, IndexWorld] = {}
        for world in self.worlds:
            for key in (world.name, world.display_name, world.apworld_id):
                folded = key.casefold().strip()
                if folded:
                    self._by_name.setdefault(folded, world)

    def lookup(self, game: str) -> IndexWorld | None:
        return self._by_name.get(game.casefold().strip())


@dataclass(frozen=True)
class EnsureResult:
    outcome: EnsureOutcome
    message: str
    apworld_id: str = ""
    version: str = ""
    dest: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome != "failed"


@dataclass
class ApworldIndexSettings:
    autodownload: bool = True
    index_url: str = DEFAULT_INDEX_ARCHIVE_URL
    index_path: Path | None = None
    raw_base: str = DEFAULT_INDEX_RAW_BASE
    custom_worlds_dir: Path = field(default_factory=default_custom_worlds_dir)
    worlds_dir: Path = field(default_factory=default_worlds_dir)
    cache_ttl_seconds: int = CACHE_TTL_SECONDS


def load_settings() -> ApworldIndexSettings:
    index_path_raw = os.environ.get("SLOWRELEASE_APWORLD_INDEX_PATH", "").strip()
    return ApworldIndexSettings(
        autodownload=autodownload_enabled(),
        index_url=os.environ.get("SLOWRELEASE_APWORLD_INDEX_URL", DEFAULT_INDEX_ARCHIVE_URL).strip()
        or DEFAULT_INDEX_ARCHIVE_URL,
        index_path=Path(index_path_raw) if index_path_raw else None,
        raw_base=os.environ.get("SLOWRELEASE_APWORLD_INDEX_RAW_BASE", DEFAULT_INDEX_RAW_BASE).strip()
        or DEFAULT_INDEX_RAW_BASE,
        custom_worlds_dir=default_custom_worlds_dir(),
        worlds_dir=default_worlds_dir(),
    )


def game_from_yaml(yaml_text: str) -> str:
    """Return the YAML `game:` value, or empty string if it cannot be parsed."""
    text = yaml_text.strip()
    if not text:
        return ""
    try:
        import yaml

        data = yaml.safe_load(text)
    except Exception:
        data = None
    if isinstance(data, dict):
        game = data.get("game")
        if isinstance(game, str):
            return game.strip()
        if isinstance(game, list) and game:
            first = game[0]
            if isinstance(first, str):
                return first.strip()
            if isinstance(first, dict) and first:
                return str(next(iter(first))).strip()
        if isinstance(game, dict) and game:
            return str(next(iter(game))).strip()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("game:"):
            value = stripped[5:].strip().strip("'\"")
            if value:
                return value
    return ""


def _version_sort_key(version: str) -> tuple:
    main, _, _build = version.partition("+")
    main, _, pre = main.partition("-")
    parts: list[tuple[int, int | str]] = []
    for piece in main.split("."):
        try:
            parts.append((0, int(piece)))
        except ValueError:
            parts.append((1, piece))
    return (tuple(parts), pre == "", pre)


def choose_version(world: IndexWorld) -> str:
    if not world.versions:
        return ""
    default = world.default_version.strip()
    if default and default in world.versions and default.lower() not in {"latest", "latest_supported"}:
        return default
    return max(world.versions, key=_version_sort_key)


def resolve_download_url(world: IndexWorld, version: str, raw_base: str) -> str:
    source = world.versions.get(version)
    if source is None:
        raise ValueError(f"No version {version} for {world.apworld_id}")
    if source.kind == "url":
        url = source.spec.replace("{{version}}", version)
        if not url and world.default_url:
            url = world.default_url.replace("{{version}}", version)
        return url
    rel = source.spec.lstrip("./")
    if rel.startswith("../"):
        rel = rel[3:]
    return f"{raw_base.rstrip('/')}/{rel.lstrip('/')}"


def _parse_version_origin(value: Any, default_url: str) -> VersionSource | None:
    if value is None:
        if default_url:
            return VersionSource("url", default_url)
        return None
    if not isinstance(value, dict):
        return None
    if "url" in value and value["url"]:
        return VersionSource("url", str(value["url"]))
    if "local" in value and value["local"]:
        return VersionSource("local", str(value["local"]))
    if default_url:
        return VersionSource("url", default_url)
    return None


def parse_world_toml(apworld_id: str, text: str, checksums: dict[str, str]) -> IndexWorld | None:
    data = tomllib.loads(text)
    name = str(data.get("name") or apworld_id)
    display = str(data.get("display_name") or name)
    supported = bool(data.get("supported", False))
    disabled = bool(data.get("disabled", False))
    default_url = str(data.get("default_url") or "")
    default_version = str(data.get("default_version") or "")
    versions_raw = data.get("versions")
    versions: dict[str, VersionSource] = {}
    if isinstance(versions_raw, dict):
        for ver, origin in versions_raw.items():
            parsed = _parse_version_origin(origin, default_url)
            if parsed is not None:
                versions[str(ver)] = parsed
    return IndexWorld(
        apworld_id=apworld_id,
        name=name,
        display_name=display,
        supported=supported,
        disabled=disabled,
        default_url=default_url,
        default_version=default_version,
        versions=versions,
        checksums=checksums,
    )


def parse_index_lock(text: str) -> dict[str, dict[str, str]]:
    data = tomllib.loads(text)
    out: dict[str, dict[str, str]] = {}
    for apworld_id, versions in data.items():
        if isinstance(versions, dict):
            out[str(apworld_id)] = {str(ver): str(digest) for ver, digest in versions.items()}
    return out


def parse_index_dir(root: Path) -> IndexCatalog:
    lock_path = root / "index.lock"
    checksums_by_world: dict[str, dict[str, str]] = {}
    if lock_path.is_file():
        checksums_by_world = parse_index_lock(lock_path.read_text(encoding="utf-8"))
    index_dir = root / "index"
    worlds: list[IndexWorld] = []
    if index_dir.is_dir():
        for toml_path in sorted(index_dir.glob("*.toml")):
            world = parse_world_toml(
                toml_path.stem,
                toml_path.read_text(encoding="utf-8"),
                checksums_by_world.get(toml_path.stem, {}),
            )
            if world is not None:
                worlds.append(world)
    return IndexCatalog(worlds=worlds, source=str(root))


def _cache_dir(custom_worlds_dir: Path) -> Path:
    return custom_worlds_dir / ".sylvanova_index"


def _catalog_cache_path(custom_worlds_dir: Path) -> Path:
    return _cache_dir(custom_worlds_dir) / "catalog.json"


def catalog_to_dict(catalog: IndexCatalog) -> dict[str, Any]:
    return {
        "fetched_at": catalog.fetched_at,
        "source": catalog.source,
        "worlds": [
            {
                "apworld_id": w.apworld_id,
                "name": w.name,
                "display_name": w.display_name,
                "supported": w.supported,
                "disabled": w.disabled,
                "default_url": w.default_url,
                "default_version": w.default_version,
                "versions": {ver: {"kind": src.kind, "spec": src.spec} for ver, src in w.versions.items()},
                "checksums": w.checksums,
            }
            for w in catalog.worlds
        ],
    }


def catalog_from_dict(data: dict[str, Any]) -> IndexCatalog:
    worlds: list[IndexWorld] = []
    for raw in data.get("worlds") or []:
        versions = {
            ver: VersionSource(src["kind"], src["spec"])
            for ver, src in (raw.get("versions") or {}).items()
        }
        worlds.append(
            IndexWorld(
                apworld_id=raw["apworld_id"],
                name=raw["name"],
                display_name=raw.get("display_name") or raw["name"],
                supported=bool(raw.get("supported", False)),
                disabled=bool(raw.get("disabled", False)),
                default_url=raw.get("default_url") or "",
                default_version=raw.get("default_version") or "",
                versions=versions,
                checksums=dict(raw.get("checksums") or {}),
            )
        )
    return IndexCatalog(
        worlds=worlds,
        fetched_at=str(data.get("fetched_at") or ""),
        source=str(data.get("source") or ""),
    )


def _cache_is_fresh(path: Path, ttl_seconds: int) -> bool:
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age = (_utc_now() - fetched_at).total_seconds()
        return 0 <= age < ttl_seconds
    except Exception:
        return False


def _extract_index_from_tarball(blob: bytes, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name.replace("\\", "/")
            parts = name.split("/")
            if len(parts) < 2:
                continue
            rest = "/".join(parts[1:])
            if rest not in {"index.toml", "index.lock"} and not (
                rest.startswith("index/") and rest.endswith(".toml") and rest.count("/") == 1
            ):
                continue
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            target = dest / rest
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(extracted.read())
    return dest


async def fetch_catalog(
    settings: ApworldIndexSettings,
    client: httpx.AsyncClient,
) -> IndexCatalog:
    if settings.index_path is not None:
        catalog = parse_index_dir(settings.index_path)
        catalog.fetched_at = _utc_now().isoformat()
        catalog.source = str(settings.index_path)
        return catalog

    cache_path = _catalog_cache_path(settings.custom_worlds_dir)
    if _cache_is_fresh(cache_path, settings.cache_ttl_seconds):
        return catalog_from_dict(json.loads(cache_path.read_text(encoding="utf-8")))

    response = await client.get(settings.index_url, timeout=INDEX_TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    extract_root = _cache_dir(settings.custom_worlds_dir) / "src"
    if extract_root.exists():
        import shutil

        shutil.rmtree(extract_root)
    _extract_index_from_tarball(response.content, extract_root)
    catalog = parse_index_dir(extract_root)
    catalog.fetched_at = _utc_now().isoformat()
    catalog.source = settings.index_url
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(catalog_to_dict(catalog)), encoding="utf-8")
    return catalog


def bundled_world_dir(game: str, worlds_dir: Path) -> Path | None:
    """Return a core world folder whose name matches the YAML game, if any."""
    if not game or not worlds_dir.is_dir():
        return None
    folded = game.casefold().strip()
    compact = folded.replace(" ", "").replace("_", "").replace("-", "")
    for child in worlds_dir.iterdir():
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        if not (child / "__init__.py").is_file():
            continue
        name = child.name.casefold()
        if name == folded or name.replace("_", "").replace("-", "") == compact:
            return child
    return None


def world_already_installed(world: IndexWorld, settings: ApworldIndexSettings) -> Path | None:
    bundled = settings.worlds_dir / world.apworld_id
    if bundled.is_dir() and (bundled / "__init__.py").is_file():
        return bundled
    custom = settings.custom_worlds_dir / f"{world.apworld_id}.apworld"
    if custom.is_file():
        return custom
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_local_source(world: IndexWorld, version: str, index_root: Path | None) -> Path | None:
    source = world.versions.get(version)
    if source is None or source.kind != "local" or index_root is None:
        return None
    candidate = (index_root / "index" / source.spec).resolve()
    if candidate.is_file():
        return candidate
    rel = source.spec.lstrip("./")
    if rel.startswith("../"):
        rel = rel[3:]
    alt = (index_root / rel).resolve()
    if alt.is_file():
        return alt
    return None


def _exclusive_lock(lock_path: Path):
    import fcntl

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _unlock(handle) -> None:
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


async def _download_url(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Refusing to download apworld from non-HTTP URL: {url}")
    async with client.stream("GET", url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as response:
        response.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as handle:
            async for chunk in response.aiter_bytes():
                handle.write(chunk)


async def install_world(
    world: IndexWorld,
    settings: ApworldIndexSettings,
    client: httpx.AsyncClient,
    *,
    log: Callable[[str], None] | None = None,
) -> EnsureResult:
    def _log(message: str) -> None:
        logger.info(message)
        if log:
            log(message)

    version = choose_version(world)
    if not version:
        return EnsureResult(
            "failed",
            f"SylvaNova index has no downloadable version for '{world.name}' ({world.apworld_id}).",
            world.apworld_id,
        )
    dest = settings.custom_worlds_dir / f"{world.apworld_id}.apworld"
    lock_handle = _exclusive_lock(dest.with_name(dest.name + ".lock"))
    try:
        existing = world_already_installed(world, settings)
        if existing is not None:
            return EnsureResult(
                "already_present",
                f"Apworld for '{world.name}' already present at {existing}.",
                world.apworld_id,
                version,
                str(existing),
            )
        url = resolve_download_url(world, version, settings.raw_base)
        _log(f"Downloading {world.apworld_id}.apworld {version} from SylvaNova index…")
        settings.custom_worlds_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=settings.custom_worlds_dir,
            suffix=".apworld.tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
        try:
            local_src = _copy_local_source(world, version, settings.index_path)
            if local_src is not None:
                tmp_path.write_bytes(local_src.read_bytes())
            else:
                await _download_url(client, url, tmp_path)
            expected = world.checksums.get(version, "").strip().lower()
            if expected:
                actual = _sha256_file(tmp_path)
                if actual != expected:
                    tmp_path.unlink(missing_ok=True)
                    return EnsureResult(
                        "failed",
                        f"Checksum mismatch for {world.apworld_id} {version}: "
                        f"expected {expected[:12]}… got {actual[:12]}…",
                        world.apworld_id,
                        version,
                    )
            os.replace(tmp_path, dest)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        _log(f"Stored {dest.name} ({version}) in {settings.custom_worlds_dir}.")
        return EnsureResult(
            "downloaded",
            f"Downloaded {world.apworld_id}.apworld {version} from the SylvaNova index.",
            world.apworld_id,
            version,
            str(dest),
        )
    finally:
        _unlock(lock_handle)


async def ensure_apworld_for_yaml(
    yaml_text: str,
    *,
    log: Callable[[str], None] | None = None,
    settings: ApworldIndexSettings | None = None,
    client: httpx.AsyncClient | None = None,
) -> EnsureResult:
    cfg = settings or load_settings()
    if not cfg.autodownload:
        return EnsureResult("disabled", "Apworld autodownload is disabled.")
    game = game_from_yaml(yaml_text)
    if not game:
        return EnsureResult("no_game", "YAML has no game field; skipping apworld autodownload.")

    bundled = bundled_world_dir(game, cfg.worlds_dir)
    if bundled is not None:
        return EnsureResult(
            "core",
            f"'{game}' is already bundled at {bundled}.",
            dest=str(bundled),
        )

    owns_client = client is None
    http = client or httpx.AsyncClient(headers={"User-Agent": "SlowReleaseWeb/apworld-index"})
    try:
        catalog = await fetch_catalog(cfg, http)
        world = catalog.lookup(game)
        if world is None:
            return EnsureResult(
                "not_in_index",
                f"'{game}' is not in the SylvaNova apworld index; leaving world loading to Archipelago.",
            )
        if world.disabled:
            return EnsureResult(
                "not_in_index",
                f"'{world.name}' is disabled in the SylvaNova index; skipping autodownload.",
                world.apworld_id,
            )
        if world.supported:
            return EnsureResult(
                "core",
                f"'{world.name}' is a supported/core world; not downloading an apworld.",
                world.apworld_id,
            )
        existing = world_already_installed(world, cfg)
        if existing is not None:
            return EnsureResult(
                "already_present",
                f"Apworld for '{world.name}' already present at {existing}.",
                world.apworld_id,
                dest=str(existing),
            )
        return await install_world(world, cfg, http, log=log)
    except Exception as exc:
        logger.exception("Apworld autodownload failed for %s", game)
        return EnsureResult("failed", f"Failed to autodownload apworld for '{game}': {exc}")
    finally:
        if owns_client:
            await http.aclose()

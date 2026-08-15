"""Script to refresh the vendored Universal Tracker from upstream releases.

Usage:
  python slowrelease_web/scripts/vendor_tracker.py [tag]

Default tag: Tracker_v0.3.3
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = REPO_ROOT / "worlds" / "tracker"
DEFAULT_TAG = "Tracker_v0.3.3"


def main(argv: list[str]) -> int:
    tag = argv[1] if len(argv) > 1 else DEFAULT_TAG
    url = f"https://github.com/FarisTheAncient/Archipelago/releases/download/{tag}/tracker.apworld"
    print(f"Downloading {url}")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        apworld = tmp_path / "tracker.apworld"
        urllib.request.urlretrieve(url, apworld)
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()
        with zipfile.ZipFile(apworld) as zf:
            zf.extractall(extract_dir)
        src = extract_dir / "tracker"
        if not src.is_dir():
            raise SystemExit("tracker/ folder missing inside apworld")
        # Drop bytecode
        pycache = src / "__pycache__"
        if pycache.exists():
            shutil.rmtree(pycache)
        if TARGET.exists():
            shutil.rmtree(TARGET)
        shutil.copytree(src, TARGET)
        (TARGET / "VENDOR.txt").write_text(
            f"{tag}\n"
            f"Source: {url}\n"
            "Vendored into worlds/tracker for Slow Release Web Manager.\n"
            "Refresh with: python slowrelease_web/scripts/vendor_tracker.py [tag]\n",
            encoding="utf-8",
        )
    print(f"Installed Universal Tracker into {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

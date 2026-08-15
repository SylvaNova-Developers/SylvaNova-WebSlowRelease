#!/usr/bin/env bash
# Build Slow Release Web Manager image from repo root.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${VERSION:-$(python3 -c "from pathlib import Path; import re; t=Path('$ROOT/slowrelease_web/__init__.py').read_text(); print(re.search(r'__version__\\s*=\\s*[\"\\']([^\"\\']+)', t).group(1))")}"
IMAGE="${IMAGE:-slowrelease-web:${VERSION}}"
cd "$ROOT"
docker build \
	-f slowrelease_web/Dockerfile \
	--build-arg "VERSION=${VERSION}" \
	-t "${IMAGE}" \
	-t "slowrelease-web:latest" \
	.
echo "Built ${IMAGE}"

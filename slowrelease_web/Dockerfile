# Slow Release Web Manager image
# Build from repo root:
#   docker build -f slowrelease_web/Dockerfile -t slowrelease-web:0.2.0 .

# ---------- UI ----------
FROM node:20-bookworm-slim AS ui
WORKDIR /ui
COPY slowrelease_web/ui/package.json slowrelease_web/ui/package-lock.json ./
RUN npm ci
COPY slowrelease_web/ui/ ./
RUN npm run build

# ---------- Runtime ----------
FROM python:3.12-slim-bookworm AS runtime

ARG VERSION=0.2.0
LABEL org.opencontainers.image.title="Slow Release Web Manager" \
	org.opencontainers.image.description="Multi-slot Slow Release workers with web UI" \
	org.opencontainers.image.source="https://github.com/chouticly/SylvaNova-WebSlowRelease" \
	org.opencontainers.image.version="${VERSION}"

ENV VIRTUAL_ENV=/opt/venv \
	PATH="/opt/venv/bin:$PATH" \
	PYTHONUNBUFFERED=1 \
	PYTHONDONTWRITEBYTECODE=1 \
	SKIP_REQUIREMENTS_UPDATE=1 \
	SLOWRELEASE_HOST=0.0.0.0 \
	SLOWRELEASE_PORT=8787 \
	SLOWRELEASE_DB=/data/slowrelease.db \
	SLOWRELEASE_MAX_WORKERS=32 \
	SLOWRELEASE_CUSTOM_WORLDS=/data/custom_worlds

WORKDIR /app

# hadolint ignore=DL3008
RUN apt-get update \
	&& apt-get install -y --no-install-recommends curl ca-certificates \
	&& rm -rf /var/lib/apt/lists/* \
	&& python -m venv "$VIRTUAL_ENV"

# Install Python deps before copying the full tree for better layer caching
COPY slowrelease_web/requirements.txt /tmp/slowrelease_web/requirements.txt
COPY slowrelease_web/requirements-docker.txt /tmp/slowrelease_web/requirements-docker.txt
RUN pip install --no-cache-dir -r /tmp/slowrelease_web/requirements-docker.txt \
	&& rm -rf /tmp/slowrelease_web

# Archipelago + Slow Release application
COPY . .
COPY --from=ui /ui/dist ./slowrelease_web/ui/dist

RUN mkdir -p /data/custom_worlds \
	&& useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
	&& chown -R appuser:appuser /data /app \
	&& chmod +x /app/slowrelease_web/docker-entrypoint.sh

USER appuser
EXPOSE 8787
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
	CMD curl -fsS "http://127.0.0.1:${SLOWRELEASE_PORT}/api/health" || exit 1

ENTRYPOINT ["/app/slowrelease_web/docker-entrypoint.sh"]
CMD ["python", "-m", "slowrelease_web"]

# Releasing Slow Release Web

Version lives in `slowrelease_web/__init__.py` (`__version__`).

## Checklist

1. Bump `__version__` (SemVer).
2. Ensure `slowrelease_web/ui` builds (`npm run build`) and dist assets match `index.html`.
3. Merge to `slowrelease_new`.
4. Tag and push:

```bash
git tag -a "slowrelease-v0.2.0" -m "Slow Release Web v0.2.0"
git push origin "slowrelease-v0.2.0"
```

5. GitHub Actions (`.github/workflows/slowrelease-docker.yml`) will:
   - Build and push `ghcr.io/chouticly/sylvanova-webslowrelease:<version>` (+ `latest` on default branch builds)
   - Create a GitHub Release with run instructions and `docker-compose.yml`

## Local verify

```bash
./slowrelease_web/scripts/docker-build.sh
docker run --rm -p 8787:8787 -v sr-data:/data slowrelease-web:0.2.0
curl -fsS http://127.0.0.1:8787/api/health
```

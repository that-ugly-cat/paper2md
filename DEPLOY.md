# Deploying paper2md

FastAPI app + one SQLite file (API keys only — the converter itself is
stateless). Ships as a Docker image because Docling pulls in torch + layout
models — bare-metal works too but the container gives you the CPU cap for
free.

## 1. Configuration

```bash
cp .env.example .env
```

Set `ADMIN_PASSWORD` to enable the admin panel (issuing/revoking API keys) —
without it, `/admin` login always rejects. Set `SECRET_KEY` to a long random
value in production (signs the admin session cookie):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`ANON_MAX_UPLOAD_MB` (default 10) caps the public web page; `API_MAX_UPLOAD_MB`
(default 50) caps requests carrying a valid `X-API-Key`.

## 2. Docker (recommended)

```bash
docker compose up -d --build
```

- Listens on `127.0.0.1:8008` (internal only — put a reverse proxy in front for TLS/public access).
- `cpus: "1.0"` in `docker-compose.yml` caps the container to half the VPS's CPU cores. Adjust if the box isn't 2 cores.
- `./model-cache` is mounted to `/root/.cache` so Docling's layout model (downloaded from Hugging Face on first conversion, a few hundred MB) survives container rebuilds — otherwise every rebuild re-downloads it.
- `./data` is mounted to `/app/data` and holds `paper2md.db` (API keys) — back it up like any SQLite file (`cp data/paper2md.db backup-$(date +%F).db`).
- **First request after a fresh deploy will be slow** (model download + load). Consider doing one warm-up `curl` right after `up -d` before pointing real traffic at it.

## 3. Reverse proxy (Caddy)

```
paper2md.borant.eu {
    reverse_proxy localhost:8008
}
```

## 4. Bare-metal (no Docker)

```bash
pip install -r requirements.txt
export ADMIN_PASSWORD="..." SECRET_KEY="..."
uvicorn main:app --host 0.0.0.0 --port 8000
```

You lose the automatic CPU cap — set it yourself if needed, e.g. `taskset -c 0` (pin to one core) or `cpulimit`.

## 5. Sanity check

```bash
curl http://localhost:8008/health
curl -F file=@some-paper.pdf http://localhost:8008/convert
```

# Deploying paper2md

FastAPI app + one SQLite file (API keys only — the converter itself is
stateless). Ships as a Docker image because Docling pulls in torch + layout
models — bare-metal works too but the container gives you the CPU cap for
free.

## 1. Prerequisites

- Docker Engine + the Docker Compose plugin (`docker compose version` should work)
- ~2 GB free disk for the image + Docling's layout model
- A reverse proxy in front if this needs to be reachable from outside `localhost` (Caddy example below)

## 2. Get the code

```bash
git clone https://github.com/that-ugly-cat/paper2md.git
cd paper2md
```

## 3. Configure

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ADMIN_PASSWORD` | to use `/admin` | unset | enables the admin panel (issuing/revoking API keys); without it, admin login always rejects |
| `SECRET_KEY` | **yes, in production** | dev placeholder | signs the admin session cookie — set a long random value |
| `ANON_MAX_UPLOAD_MB` | no | `10` | upload cap for the public web page (no API key) |
| `API_MAX_UPLOAD_MB` | no | `50` | upload cap for requests carrying a valid `X-API-Key` |
| `MAX_QUEUE_DEPTH` | no | `10` | requests queued behind the in-flight conversion before `503` |
| `CONVERT_TIMEOUT_SECONDS` | no | `300` | per-conversion timeout |

Generate a secret:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 4. Build and start

```bash
docker compose up -d --build
```

This builds the image, creates `./data` (SQLite, API keys) and `./model-cache`
(Docling's downloaded models) on the host, and starts the container listening
on `127.0.0.1:8008` — internal only, not exposed outside the host.

Check it came up clean:

```bash
docker compose logs -f
```

- `cpus: "1.0"` in `docker-compose.yml` caps the container to half the CPU cores of a 2-core box. Adjust that value (and `mem_limit`) if your box has a different shape.
- `./model-cache` persists Docling's layout model (downloaded from Hugging Face on first conversion, a few hundred MB) across rebuilds — otherwise every rebuild re-downloads it.
- `./data` holds `paper2md.db` (API keys only — the converter itself keeps no state). Back it up like any SQLite file: `cp data/paper2md.db backup-$(date +%F).db`.
- **The first conversion after a fresh deploy will be slow** (model download + load). Do one warm-up request before pointing real traffic at it:
  ```bash
  curl -F file=@some-paper.pdf http://127.0.0.1:8008/convert
  ```

## 5. Reverse proxy (Caddy example)

Replace `yourdomain.example` with the real domain:

```
yourdomain.example {
    reverse_proxy localhost:8008
}
```

Reload Caddy, then confirm both the web page and the API are reachable through the proxy:

```bash
curl https://yourdomain.example/health
```

Nginx equivalent:

```nginx
server {
    listen 443 ssl;
    server_name yourdomain.example;
    location / {
        proxy_pass http://127.0.0.1:8008;
        proxy_set_header Host $host;
    }
}
```

## 6. Updating

```bash
git pull
docker compose up -d --build
```

`./data` and `./model-cache` are host-mounted, so API keys and the cached model survive the rebuild.

## 7. Bare-metal (no Docker)

```bash
pip install -r requirements.txt
export ADMIN_PASSWORD="..." SECRET_KEY="..."
uvicorn main:app --host 0.0.0.0 --port 8000
```

You lose the automatic CPU cap — set it yourself if needed, e.g. `taskset -c 0` (pin to one core) or `cpulimit`.

## 8. Sanity check

```bash
curl http://localhost:8008/health
curl -F file=@some-paper.pdf http://localhost:8008/convert
```

Then open the site in a browser and confirm the admin login (top-right field) works with `ADMIN_PASSWORD`.

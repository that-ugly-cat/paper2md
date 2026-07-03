# Deploying paper2md

Single FastAPI app, no database. Ships as a Docker image because Docling
pulls in torch + layout models — bare-metal works too but the container
gives you the CPU cap for free.

## 1. Configuration

```bash
cp .env.example .env
```

Set `API_KEY` in `.env` if this should require auth (recommended once it's
reachable from anywhere other than localhost — leave empty only for
strictly local/dev use).

## 2. Docker (recommended)

```bash
docker compose up -d --build
```

- Listens on `127.0.0.1:8008` (internal only — put a reverse proxy in front for TLS/public access).
- `cpus: "1.0"` in `docker-compose.yml` caps the container to half the VPS's CPU cores. Adjust if the box isn't 2 cores.
- `./model-cache` is mounted to `/root/.cache` so Docling's layout model (downloaded from Hugging Face on first conversion, a few hundred MB) survives container rebuilds — otherwise every rebuild re-downloads it.
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
export API_KEY="..."
uvicorn main:app --host 0.0.0.0 --port 8000
```

You lose the automatic CPU cap — set it yourself if needed, e.g. `taskset -c 0` (pin to one core) or `cpulimit`.

## 5. Sanity check

```bash
curl http://localhost:8008/health
curl -F file=@some-paper.pdf http://localhost:8008/convert
```

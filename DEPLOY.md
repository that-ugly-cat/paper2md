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
| `AUTH_MODE` | no | `local` | `local` = admin password, as always. `gateway` = trust an SSO gate in front (see §9) |
| `BORANT_TRUSTED_PROXY` | in `gateway` | `127.0.0.1` | the address the proxy connects from; headers from anywhere else are ignored |
| `BORANT_LOGOUT_URL` | no | `https://id.borant.eu/logout` | where "log out" goes in `gateway` mode |

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

## 9. Behind an SSO gate (`AUTH_MODE=gateway`)

Optional, and off unless you switch it on. In `gateway` mode paper2md stops
checking the admin password and instead believes the identity headers set by a
`forward_auth` gate sitting in front of it: reaching a route at all means the
gate already found a valid session. `/admin/login` redirects to the home page
so there is no second way in, and "log out" goes to `BORANT_LOGOUT_URL` so the
central session is revoked and not just the local cookie.

**`local` stays the default, and that is not an accident.** An app that
believes `X-Borant-Sub` with nothing in front of it lets in anyone who sends
that header. Turn `gateway` on only once the gate is actually there, and leave
`ADMIN_PASSWORD` populated: it is what you fall back to when you set
`AUTH_MODE=local` again.

`BORANT_TRUSTED_PROXY` is the second lock, and **it is the setting people get
wrong**. Under Docker the proxy runs on the host, so the container does not see
`127.0.0.1` — it sees the bridge gateway of a Docker network. When the
container is attached to more than one network (this one joins
`paper2md-shared` for co-located clients) the gateway that shows up is not
necessarily the one you would guess, so list them all rather than reasoning
about which is used:

```bash
for n in $(docker inspect paper2md --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'); do
  docker network inspect "$n" --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'
done
```

Then confirm against reality rather than trusting the list, because which
gateway shows up is not always the one you would pick — on our own deployment
it is the shared network's, not the app's default one:

```bash
curl -s -o /dev/null http://127.0.0.1:8008/health && docker logs paper2md 2>&1 | tail -1
```

The address in that log line is the value to use.

Only gateway addresses belong in this list, never a whole subnet: sibling
containers live in the same subnets and must not be able to assert an identity.

These addresses can change if a Compose network is ever recreated. When that
happens `/admin` answers `503` with a message saying exactly this — that is the
symptom to recognise.

Sibling containers converting over `paper2md-shared` come from a different
subnet, so they are not trusted for identity headers. They do not need to be:
they authenticate to `/convert` with `X-API-Key`, which `gateway` mode does not
touch.

Rollback is two lines and no data migration:

```bash
sed -i 's/^AUTH_MODE=gateway/AUTH_MODE=local/' .env
docker compose up -d
```

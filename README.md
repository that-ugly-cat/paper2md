# paper2md

A minimal service that turns a scientific PDF into clean text (plus raw
Markdown) using [Docling](https://github.com/docling-project/docling)'s
layout model — reading order reconstruction, dropped tables/images/captions/
headers/footers/footnotes, rejoined sentences, stripped copyright/download
boilerplate.

Two ways in:
- **Public web page** (`/`) — drop a PDF, get text back, download `.md`/`.txt`. No login.
- **API** (`POST /convert`) — for scripts and other tools (e.g. feeding papers into AutoCode). Open by default; issue named keys via the admin panel if you want to track/restrict programmatic callers.

Adapted from the `ReadPDF v2` notebook — same pipeline, wrapped in a FastAPI
service instead of a notebook loop.

## API

### `POST /convert`

Multipart form, exactly one of:
- `file` — the PDF to upload
- `url` — a link to a PDF the server should download

Optional form fields:
- `remove_references` (bool, default `true`) — drop the bibliography section onward
- `format` (`json` default, or `text`) — `text` returns plain clean text; `json` returns:
  ```json
  {"text": "...", "markdown": "...", "blocks": 123, "pages": 12, "seconds": 8.4}
  ```

No `X-API-Key` header → treated as anonymous/web use, capped at `ANON_MAX_UPLOAD_MB`
(default 10 MB). A valid, active key issued from `/admin` → capped at
`API_MAX_UPLOAD_MB` (default 50 MB). An invalid/revoked key → `401`.

```bash
curl -F file=@paper.pdf http://localhost:8008/convert
curl -H "X-API-Key: p2m_..." -F url=https://example.org/paper.pdf -F format=text http://localhost:8008/convert
```

### `GET /health`

Liveness check, no auth.

## Admin panel

`/` has a small "Admin password" field in the top-right corner (only visible
when logged out). Logging in sets a session cookie and unlocks `/admin`, where
you can issue API keys (name, email, notes) and revoke/delete them. Requires
`ADMIN_PASSWORD` to be set — see `.env.example`. State lives in one SQLite
file under `data/`.

## Why is this so slow

Because it's absurdly overkill, and it's overkill for a reason: a huge share
of published scientific PDFs are formatted like a crime scene. Two-column
layouts that fall apart under naive extraction. Headers, footers and
footnotes bleeding into the body text. Sentences split across a page break
by a stray copyright notice. Reading order that only makes sense once you
already know what the paper says.

None of that is exotic. It's most of the literature. Getting clean text out
of it reliably, without babysitting every single PDF by hand, means running
a full document-layout model instead of just grepping bytes out of a file.
Hence the 10-30 seconds a paper, hence the CPU cap, hence the queue.

Journals that publish proper structured XML instead of print-shaped PDFs
(JMIR, PLOS, and a handful of others) make all of this unnecessary: reading
order isn't a mystery, nothing needs reconstructing, the metadata is just
there. More publishers doing that would put this tool out of a job
tomorrow. Until then: it's slow because the input is bad, not because
Docling is lazy.

## Design notes

- **Only born-digital PDFs.** OCR is off — scanned/image-only PDFs won't extract text. Kept out on purpose to avoid the extra OCR engine weight/complexity; revisit if it's actually needed.
- **One conversion at a time.** A single-worker thread pool serializes all `/convert` calls — this *is* the queue. Requests beyond `MAX_QUEUE_DEPTH` (default 10) get `503` instead of piling up.
- **CPU-capped.** See `docker-compose.yml` — `cpus: "1.0"` limits the container to half the VPS's cores, and BLAS/torch thread pools are pinned to 1, so a conversion can't starve the other services on the box.
- **Fake progress bar on the web page.** Conversion is one synchronous call (~10-30s for a typical paper); the frontend shows a time-based progress estimate, not real per-page progress. A real progress meter would need a job+polling API — not built.
- Conversions time out after `CONVERT_TIMEOUT_SECONDS` (default 300s).

See `DEPLOY.md` for running it.

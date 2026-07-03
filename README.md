# paper2md

A single-endpoint service that turns a scientific PDF into clean text (plus
raw Markdown) using [Docling](https://github.com/docling-project/docling)'s
layout model — reading order reconstruction, dropped tables/images/captions/
headers/footers/footnotes, rejoined sentences, stripped copyright/download
boilerplate. No UI, no database: it's meant to be called by scripts and other
tools (e.g. feeding papers into AutoCode).

Adapted from the `ReadPDF v2` notebook — same pipeline, wrapped in a FastAPI
endpoint instead of a notebook loop.

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

If `API_KEY` is set (see `.env.example`), requests must send `X-API-Key: <value>`.

```bash
curl -F file=@paper.pdf http://localhost:8008/convert
curl -F url=https://example.org/paper.pdf -F format=text http://localhost:8008/convert
```

### `GET /health`

Liveness check, no auth.

## Design notes

- **Only born-digital PDFs.** OCR is off — scanned/image-only PDFs won't extract text. Kept out on purpose to avoid the extra OCR engine weight/complexity; revisit if it's actually needed.
- **One conversion at a time.** A single-worker thread pool serializes all `/convert` calls — this *is* the queue. Requests beyond `MAX_QUEUE_DEPTH` (default 10) get `503` instead of piling up.
- **CPU-capped.** See `docker-compose.yml` — `cpus: "1.0"` limits the container to half the VPS's cores, and BLAS/torch thread pools are pinned to 1, so a conversion can't starve the other services on the box.
- Upload/download size capped at `MAX_UPLOAD_MB` (default 50 MB); conversions time out after `CONVERT_TIMEOUT_SECONDS` (default 300s).

See `DEPLOY.md` for running it.

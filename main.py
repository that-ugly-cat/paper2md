import asyncio
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from converter import make_converter, pdf_to_clean_text

# Docling/torch/BLAS would otherwise each try to grab all cores; since jobs
# already run one at a time (single-worker executor below), pin internal
# thread pools to 1 so a single conversion can't oversubscribe the box.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
try:
    import torch
    torch.set_num_threads(1)
except ImportError:
    pass

API_KEY = os.environ.get("API_KEY", "")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024
MAX_QUEUE_DEPTH = int(os.environ.get("MAX_QUEUE_DEPTH", "10"))
CONVERT_TIMEOUT_SECONDS = int(os.environ.get("CONVERT_TIMEOUT_SECONDS", "300"))
DOWNLOAD_TIMEOUT_SECONDS = 30

app = FastAPI(title="paper2md")

# One worker => conversions are processed strictly one at a time (the queue).
# Combined with the thread pins above, this keeps the service to roughly one
# core's worth of CPU even under a burst of requests.
_executor = ThreadPoolExecutor(max_workers=1)
_converter = None
_pending = 0
_pending_lock = asyncio.Lock()


def _get_converter():
    global _converter
    if _converter is None:
        _converter = make_converter()
    return _converter


def _check_api_key(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "Invalid or missing X-API-Key")


def _download_pdf(url: str, dest: Path) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "Only http/https URLs are supported")
    try:
        resp = requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(400, f"Could not fetch URL: {exc}") from exc

    written = 0
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
            f.write(chunk)

    if written == 0:
        raise HTTPException(400, "Downloaded file is empty")


async def _save_upload(file: UploadFile, dest: Path) -> None:
    written = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 256):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
            f.write(chunk)
    if written == 0:
        raise HTTPException(400, "Uploaded file is empty")


def _assert_is_pdf(path: Path) -> None:
    with open(path, "rb") as f:
        header = f.read(5)
    if header != b"%PDF-":
        raise HTTPException(400, "File does not look like a PDF")


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/convert")
async def convert(
    file: UploadFile | None = File(None),
    url: str | None = Form(None),
    remove_references: bool = Form(True),
    format: str = Form("json"),
    x_api_key: str | None = Header(None),
):
    """Accepts either a multipart `file` upload or a form field `url`. Returns
    clean text + raw markdown as JSON, or plain text if format=text."""
    _check_api_key(x_api_key)

    if bool(file) == bool(url):
        raise HTTPException(400, "Provide exactly one of: file upload, url")

    global _pending
    async with _pending_lock:
        if _pending >= MAX_QUEUE_DEPTH:
            raise HTTPException(503, "Queue is full, try again shortly")
        _pending += 1

    try:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "input.pdf"
            if file is not None:
                await _save_upload(file, pdf_path)
            else:
                _download_pdf(url, pdf_path)
            _assert_is_pdf(pdf_path)

            loop = asyncio.get_running_loop()
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        _executor, pdf_to_clean_text, _get_converter(), pdf_path, remove_references, False
                    ),
                    timeout=CONVERT_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                raise HTTPException(504, "Conversion timed out")
    finally:
        async with _pending_lock:
            _pending -= 1

    if format == "text":
        return PlainTextResponse(result["text"])
    return JSONResponse(result)

import asyncio
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler as _default_http_exc
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from auth import check_admin_password, create_admin_token, is_admin_request, require_admin
from converter import make_converter, pdf_to_clean_text
from models import ApiKey, generate_key, get_db, init_db

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

ANON_MAX_UPLOAD_BYTES = int(os.environ.get("ANON_MAX_UPLOAD_MB", "10")) * 1024 * 1024
API_MAX_UPLOAD_BYTES = int(os.environ.get("API_MAX_UPLOAD_MB", "50")) * 1024 * 1024
MAX_QUEUE_DEPTH = int(os.environ.get("MAX_QUEUE_DEPTH", "10"))
CONVERT_TIMEOUT_SECONDS = int(os.environ.get("CONVERT_TIMEOUT_SECONDS", "300"))
DOWNLOAD_TIMEOUT_SECONDS = 30

app = FastAPI(title="paper2md")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

init_db()

# One worker => conversions are processed strictly one at a time (the queue).
# Combined with the thread pins above, this keeps the service to roughly one
# core's worth of CPU even under a burst of requests.
_executor = ThreadPoolExecutor(max_workers=1)
_converter = None
_pending = 0
_pending_lock = asyncio.Lock()


@app.exception_handler(HTTPException)
async def redirect_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 302 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=302)
    return await _default_http_exc(request, exc)


def _get_converter():
    global _converter
    if _converter is None:
        _converter = make_converter()
    return _converter


def _check_api_key(x_api_key: str | None, db: Session) -> int:
    """Returns the max upload size allowed for this caller. No header = anonymous
    (web) use, capped tighter. A header must match an active issued key."""
    if not x_api_key:
        return ANON_MAX_UPLOAD_BYTES
    record = db.query(ApiKey).filter(ApiKey.key == x_api_key, ApiKey.active == True).first()  # noqa: E712
    if not record:
        raise HTTPException(401, "Invalid or revoked X-API-Key")
    record.last_used_at = datetime.utcnow()
    db.commit()
    return API_MAX_UPLOAD_BYTES


def _download_pdf(url: str, dest: Path, max_bytes: int) -> None:
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
            if written > max_bytes:
                raise HTTPException(413, f"File exceeds {max_bytes // (1024 * 1024)} MB limit")
            f.write(chunk)

    if written == 0:
        raise HTTPException(400, "Downloaded file is empty")


async def _save_upload(file: UploadFile, dest: Path, max_bytes: int) -> None:
    written = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 256):
            written += len(chunk)
            if written > max_bytes:
                raise HTTPException(413, f"File exceeds {max_bytes // (1024 * 1024)} MB limit")
            f.write(chunk)
    if written == 0:
        raise HTTPException(400, "Uploaded file is empty")


def _assert_is_pdf(path: Path) -> None:
    with open(path, "rb") as f:
        header = f.read(5)
    if header != b"%PDF-":
        raise HTTPException(400, "File does not look like a PDF")


# ── Public frontend ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, admin_error: int = 0):
    return templates.TemplateResponse(request, "index.html", {
        "anon_max_mb": ANON_MAX_UPLOAD_BYTES // (1024 * 1024),
        "admin_error": bool(admin_error),
        "is_admin": is_admin_request(request),
    })


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
    db: Session = Depends(get_db),
):
    """Accepts either a multipart `file` upload or a form field `url`. Returns
    clean text + raw markdown as JSON, or plain text if format=text."""
    max_bytes = _check_api_key(x_api_key, db)

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
                await _save_upload(file, pdf_path, max_bytes)
            else:
                _download_pdf(url, pdf_path, max_bytes)
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


# ── Admin ─────────────────────────────────────────────────────────────────────

@app.post("/admin/login")
async def admin_login(password: str = Form(...)):
    if not check_admin_password(password):
        return RedirectResponse("/?admin_error=1", status_code=302)
    resp = RedirectResponse("/admin", status_code=302)
    resp.set_cookie("admin_token", create_admin_token(), httponly=True, samesite="lax", max_age=86400 * 7)
    return resp


@app.get("/admin/logout")
async def admin_logout():
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie("admin_token")
    return resp


@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request, _: None = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()
    keys = [{
        "id": r.id,
        "name": f"{r.first_name} {r.last_name}",
        "email": r.email,
        "key": r.key,
        "notes": r.notes or "",
        "active": r.active,
        "created_at": r.created_at.strftime("%b %d, %Y"),
        "last_used_at": r.last_used_at.strftime("%b %d, %Y") if r.last_used_at else "never",
    } for r in rows]
    return templates.TemplateResponse(request, "admin.html", {"keys": keys})


@app.post("/admin/keys")
async def create_key(
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    notes: str = Form(""),
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
):
    record = ApiKey(
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        email=email.strip(),
        notes=notes.strip(),
        key=generate_key(),
    )
    db.add(record)
    db.commit()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/keys/{key_id}/toggle")
async def toggle_key(key_id: int, _: None = Depends(require_admin), db: Session = Depends(get_db)):
    record = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if record:
        record.active = not record.active
        db.commit()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/keys/{key_id}/delete")
async def delete_key(key_id: int, _: None = Depends(require_admin), db: Session = Depends(get_db)):
    record = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if record:
        db.delete(record)
        db.commit()
    return RedirectResponse("/admin", status_code=302)

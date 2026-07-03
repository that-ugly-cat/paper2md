import os
from datetime import datetime, timedelta

import jwt
from fastapi import HTTPException, Request

SECRET = os.environ.get("SECRET_KEY", "paper2md-dev-secret-change-me")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ALGO = "HS256"


def admin_configured() -> bool:
    return bool(ADMIN_PASSWORD)


def check_admin_password(password: str) -> bool:
    return admin_configured() and password == ADMIN_PASSWORD


def create_admin_token() -> str:
    payload = {"sub": "admin", "exp": datetime.utcnow() + timedelta(days=7)}
    return jwt.encode(payload, SECRET, algorithm=ALGO)


def require_admin(request: Request) -> None:
    if not is_admin_request(request):
        raise HTTPException(302, headers={"Location": "/"})


def is_admin_request(request: Request) -> bool:
    token = request.cookies.get("admin_token")
    if not token:
        return False
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGO])
    except Exception:
        return False
    return payload.get("sub") == "admin"

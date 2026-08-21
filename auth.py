import ipaddress
import logging
import os
from datetime import datetime, timedelta

import jwt
from fastapi import HTTPException, Request

log = logging.getLogger("paper2md.auth")

SECRET = os.environ.get("SECRET_KEY", "paper2md-dev-secret-change-me")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ALGO = "HS256"

# Two ways of recognising the admin, and `local` is the default on purpose: an
# app that believes an identity header without a gate in front of it lets in
# anyone who sends that header. The gateway path stays dead code until someone
# turns it on deliberately.
#
#   local     the admin password in the environment, as it has always worked
#   gateway   an upstream SSO gate vouches for the caller via X-Borant-*
#
AUTH_MODE = os.environ.get("AUTH_MODE", "local").strip().lower()

# In gateway mode the identity headers are believed only when they arrive from
# here — the reverse proxy, never the internet. Under Docker this is the bridge
# gateway of the app's network and NOT 127.0.0.1; DEPLOY.md shows how to read
# the right value off a running host. Accepts single addresses or CIDRs,
# comma-separated.
TRUSTED_PROXY = os.environ.get("BORANT_TRUSTED_PROXY", "127.0.0.1")


def _parse_trusted(raw: str) -> list:
    nets = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            nets.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            log.warning("BORANT_TRUSTED_PROXY: ignoring %r, not an address or CIDR", chunk)
    return nets


TRUSTED_PROXIES = _parse_trusted(TRUSTED_PROXY)


def gateway_mode() -> bool:
    return AUTH_MODE == "gateway"


def admin_configured() -> bool:
    return bool(ADMIN_PASSWORD)


def check_admin_password(password: str) -> bool:
    return admin_configured() and password == ADMIN_PASSWORD


def create_admin_token() -> str:
    payload = {"sub": "admin", "exp": datetime.utcnow() + timedelta(days=7)}
    return jwt.encode(payload, SECRET, algorithm=ALGO)


def _from_trusted_proxy(request: Request) -> bool:
    peer = request.client.host if request.client else None
    if not peer:
        return False
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in TRUSTED_PROXIES)


def gateway_identity(request: Request) -> dict | None:
    """Who the gate says this is, or None. Consulted only in gateway mode, and
    only for requests that actually came from the trusted proxy."""
    if not gateway_mode():
        return None
    sub = request.headers.get("x-borant-sub")
    if not sub:
        return None
    if not _from_trusted_proxy(request):
        log.warning(
            "X-Borant-Sub from %s, which is not in BORANT_TRUSTED_PROXY (%s): ignored",
            request.client.host if request.client else "?", TRUSTED_PROXY,
        )
        return None
    return {
        "sub": sub,
        "email": request.headers.get("x-borant-email", ""),
        "name": request.headers.get("x-borant-name", ""),
        "level": request.headers.get("x-borant-level", ""),
    }


def require_admin(request: Request) -> None:
    if is_admin_request(request):
        return
    if gateway_mode():
        # There is no local login to fall back to here, so bouncing to "/"
        # would be a dead end with nothing on screen to explain it. The usual
        # cause is BORANT_TRUSTED_PROXY pointing at the wrong address after a
        # Docker network was recreated.
        raise HTTPException(
            503,
            "Gateway mode: no valid identity in the X-Borant-* headers. Check that the "
            "gate really sits in front of this app and that BORANT_TRUSTED_PROXY lists "
            "the address the reverse proxy connects from.",
        )
    raise HTTPException(302, headers={"Location": "/"})


def is_admin_request(request: Request) -> bool:
    if gateway_mode():
        # Reaching a gated route at all means the gate found a valid session
        # and a grant for this host, so there is nothing left for us to check.
        # The local cookie is deliberately not consulted: a stale one must not
        # outlive a session revoked centrally.
        return gateway_identity(request) is not None
    token = request.cookies.get("admin_token")
    if not token:
        return False
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGO])
    except Exception:
        return False
    return payload.get("sub") == "admin"

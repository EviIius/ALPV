"""Sign-in, first-run setup, sessions and passphrase changes."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request, Response

from .. import APP_NAME, __version__, security

router = APIRouter(prefix="/api/auth")


def _is_secure(request: Request) -> bool:
    return request.state.scheme == "https"


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        security.SESSION_COOKIE, token, max_age=security.SESSION_DAYS * 86400, httponly=True, samesite="strict",
        secure=_is_secure(request), path="/",
    )


def _local(request: Request) -> bool:
    return security.classify_ip(request.state.client_ip) == "loopback"


@router.get("/status")
def status(request: Request):
    token = request.cookies.get(security.SESSION_COOKIE)
    configured = security.is_configured()
    return {
        "app": APP_NAME,
        "version": __version__,
        "configured": configured,
        "authenticated": bool(configured and security.check_session(token)),
        "setup_needs_code": not configured and not _local(request),
        "connection": security.classify_ip(request.state.client_ip),
        "secure": _is_secure(request),
    }


@router.post("/setup")
def setup(request: Request, response: Response, payload: dict = Body(...)):
    if security.is_configured():
        raise HTTPException(409, "Daybook is already set up. Sign in instead.")
    if not _local(request):
        code = str(payload.get("code") or "").strip().replace(" ", "")
        if code != security.setup_code():
            raise HTTPException(403, "That setup code doesn't match. It's printed in the terminal where Daybook is running.")
    err = security.validate_new_passphrase(payload.get("passphrase") or "")
    if err:
        raise HTTPException(400, err)
    security.set_passphrase(payload["passphrase"])
    token, _ = security.create_session(request.headers.get("user-agent", ""), request.state.client_ip)
    _set_cookie(response, request, token)
    return {"ok": True}


@router.post("/login")
def login(request: Request, response: Response, payload: dict = Body(...)):
    key = request.state.client_ip or "?"
    wait = security.throttle.wait_seconds(key)
    if wait:
        raise HTTPException(429, f"Too many attempts. Try again in {wait} seconds.")
    if not security.is_configured():
        raise HTTPException(409, "Daybook isn't set up yet.")
    if not security.check_passphrase(payload.get("passphrase") or ""):
        security.throttle.fail(key)
        raise HTTPException(401, "That passphrase isn't right.")
    security.throttle.ok(key)
    token, _ = security.create_session(request.headers.get("user-agent", ""), request.state.client_ip)
    _set_cookie(response, request, token)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response):
    security.end_session(request.state.token)
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/sessions")
def sessions(request: Request):
    return security.list_sessions(request.state.token)


@router.post("/sessions/revoke-others")
def revoke_others(request: Request):
    return {"ended": security.end_other_sessions(request.state.token)}


@router.post("/passphrase")
def change_passphrase(request: Request, payload: dict = Body(...)):
    if not security.check_passphrase(payload.get("current") or ""):
        raise HTTPException(403, "Your current passphrase isn't right.")
    err = security.validate_new_passphrase(payload.get("new") or "")
    if err:
        raise HTTPException(400, err)
    security.set_passphrase(payload["new"])
    ended = security.end_other_sessions(request.state.token)
    return {"ok": True, "signed_out": ended}

"""Daybook web server (FastAPI).

Security model
--------------
* Every /api route except sign-in requires a session cookie (HttpOnly,
  SameSite=Strict). State-changing requests must also carry the
  `X-Daybook: 1` header, which cross-site pages cannot send — CSRF defence.
* A strict Content-Security-Policy means the browser may only talk to this
  server: no CDNs, no analytics, no third-party requests of any kind.
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from anyio import to_thread
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from . import APP_NAME, __version__, db, security
from .bus import bus
from .config import STATIC_DIR, Paths, paths as default_paths

# PDF.js ships as ES modules (.mjs); some Python versions don't know the type.
mimetypes.add_type("text/javascript", ".mjs")

log = logging.getLogger("daybook")

PUBLIC_API = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/setup"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self'; connect-src 'self'; media-src 'self' blob:; worker-src 'self'; manifest-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
SECURITY_HEADERS = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": "camera=(), geolocation=(), microphone=(self), payment=(), usb=(), interest-cohort=()",
}


def client_ip(scope) -> str:
    """Peer address; trust X-Forwarded-For only from a proxy on this machine
    (e.g. `tailscale serve`), so a proxied request is never treated as local."""
    peer = (scope.get("client") or ("", 0))[0] or ""
    if security.classify_ip(peer) == "loopback":
        for k, v in scope.get("headers", []):
            if k == b"x-forwarded-for" and v:
                return v.decode().split(",")[0].strip()
    return peer


def request_scheme(scope) -> str:
    peer = (scope.get("client") or ("", 0))[0] or ""
    if security.classify_ip(peer) == "loopback":
        for k, v in scope.get("headers", []):
            if k == b"x-forwarded-proto" and v:
                return v.decode().split(",")[0].strip().lower()
    return scope.get("scheme", "http")


def _cookie(scope, name: str) -> str | None:
    for k, v in scope.get("headers", []):
        if k == b"cookie":
            for part in v.decode("latin-1").split(";"):
                if "=" in part:
                    ck, cv = part.strip().split("=", 1)
                    if ck == name:
                        return cv
    return None


class GuardMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path: str = scope["path"]
        method: str = scope["method"]
        is_api = path.startswith("/api/")
        scope.setdefault("state", {})
        scope["state"]["client_ip"] = client_ip(scope)
        scope["state"]["scheme"] = request_scheme(scope)
        if is_api and path not in PUBLIC_API:
            if method not in SAFE_METHODS:
                hdr = dict(scope.get("headers", [])).get(b"x-daybook")
                if hdr != b"1":
                    return await self._json(send, 403, {"detail": "Missing request header."})
            token = _cookie(scope, security.SESSION_COOKIE)
            session = await to_thread.run_sync(security.check_session, token) if token else None
            if not session:
                return await self._json(send, 401, {"detail": "Sign in to continue."})
            scope["state"]["session"] = session
            scope["state"]["token"] = token

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in SECURITY_HEADERS.items():
                    headers.setdefault(k, v)
                if is_api:
                    headers["cache-control"] = "no-store"
                elif path.startswith("/fonts/"):
                    headers["cache-control"] = "public, max-age=2592000"
                else:
                    headers["cache-control"] = "no-cache"
            await send(message)

        return await self.app(scope, receive, send_wrapper)

    @staticmethod
    async def _json(send, status: int, body: dict) -> None:
        import json

        payload = json.dumps(body).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
        headers += [(k.encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": payload})


def create_app(data_dir: Path | None = None, *, background: bool = True) -> FastAPI:
    p = Paths(data_dir) if data_dir else default_paths

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        p.ensure()
        db.configure(p)
        db.migrate()
        from .services import backup, scheduler as sched

        backup.seed_defaults()
        bus.bind(asyncio.get_running_loop())
        if not security.is_configured():
            log.warning("First run: open Daybook in a browser to choose a passphrase. Setup code: %s", security.setup_code())
        if background:
            await sched.scheduler.start()
        try:
            yield
        finally:
            if background:
                await sched.scheduler.stop()

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(x) for x in first.get("loc", [])[1:])
        return JSONResponse({"detail": f"Check {where or 'the request'}: {first.get('msg', 'invalid value')}."}, status_code=422)

    from .services import ai as ai_svc, converters, events as ev_svc, library as library_svc, notes as notes_svc, reader as reader_svc, tasks as tasks_svc

    def _err(status: int, fallback: str):
        async def handler(_req: Request, exc: Exception):
            msg = fallback if status == 404 else (str(exc) or fallback)
            body = {"detail": msg}
            if isinstance(exc, notes_svc.Conflict):
                body["note"] = exc.note
            return JSONResponse(body, status_code=status)
        return handler

    for cls, status, fallback in [
        (notes_svc.NotFound, 404, "That note no longer exists."),
        (ev_svc.NotFound, 404, "That event no longer exists."),
        (tasks_svc.NotFound, 404, "That task no longer exists."),
        (notes_svc.Conflict, 409, "This note changed on another device."),
        (notes_svc.Locked, 423, "This note is locked."),
        (PermissionError, 403, "Not allowed."),
        (ValueError, 400, "Something in the request isn't valid."),
        (ai_svc.AIBlocked, 403, "Only AI on your own devices is allowed."),
        (ai_svc.AIUnavailable, 503, "Local AI isn't reachable."),
        (ai_svc.AIError, 502, "Local AI returned an error."),
        (converters.ConversionError, 422, "That file couldn't be converted."),
        (library_svc.LibraryError, 400, "Something went wrong in your library folder."),
        (reader_svc.NotReadable, 422, "This can't be shown in the reader."),
    ]:
        app.add_exception_handler(cls, _err(status, fallback))

    from .routers import ai, auth, calendar, feed, glossary, home, imports, library, notes, reader, settings, tasks, updates

    for r in (auth.router, notes.router, calendar.router, tasks.router, home.router, imports.router, ai.router, settings.router,
              library.router, reader.router, feed.router, glossary.router, updates.router):
        app.include_router(r)

    @app.get("/api/health")
    def health():
        # Also says whether newer code is waiting on disk, so any signed-in device can offer a restart.
        from .services import updates as updates_svc

        st = updates_svc.status()
        return {"status": "ok", "app": APP_NAME, "version": __version__, "update_ready": st["update_ready"],
                "restartable": st["restartable"], "started_at": st["started_at"]}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    app.add_middleware(GuardMiddleware)
    return app

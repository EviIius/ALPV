"""Authentication, sessions and encryption helpers.

* Passphrase hashed with scrypt (stdlib), never stored in plain text.
* Sessions are random 256-bit tokens; only their SHA-256 is stored.
* First-run setup needs a one-time code printed in the server console,
  unless the browser is on the server machine itself.
* Locked notes and encrypted backups use AES-256-GCM with a key derived from
  the vault passphrase via scrypt.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import threading
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import db
from .util import now_ms

SESSION_COOKIE = "daybook_session"
SESSION_DAYS = 30
_SCRYPT = {"n": 2**15, "r": 8, "p": 1}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _scrypt(secret: str, salt: bytes, n: int, r: int, p: int, dklen: int = 32) -> bytes:
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=128 * 1024 * 1024)


# ---------------------------------------------------------------------------
# Passphrase
# ---------------------------------------------------------------------------
def hash_passphrase(passphrase: str) -> str:
    salt = os.urandom(16)
    dk = _scrypt(passphrase, salt, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${_b64(salt)}${_b64(dk)}"


def verify_passphrase(passphrase: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        test = _scrypt(passphrase, _unb64(salt), int(n), int(r), int(p), len(_unb64(dk)))
        return hmac.compare_digest(test, _unb64(dk))
    except (ValueError, TypeError):
        return False


def is_configured() -> bool:
    return db.scalar("SELECT count(*) FROM auth") > 0


def set_passphrase(passphrase: str) -> None:
    ts = now_ms()
    with db.tx() as c:
        c.execute(
            "INSERT INTO auth(id, pass_hash, created_at, updated_at) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET pass_hash = excluded.pass_hash, updated_at = excluded.updated_at",
            (hash_passphrase(passphrase), ts, ts),
        )


def check_passphrase(passphrase: str) -> bool:
    row = db.q1("SELECT pass_hash FROM auth WHERE id = 1")
    return bool(row) and verify_passphrase(passphrase, row["pass_hash"])


def validate_new_passphrase(p: str) -> str | None:
    if len(p or "") < 8:
        return "Use at least 8 characters."
    if len(p) > 512:
        return "That passphrase is too long."
    return None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_agent: str = "", ip: str = "") -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    ts = now_ms()
    expires = ts + SESSION_DAYS * 86400 * 1000
    with db.tx() as c:
        c.execute(
            "INSERT INTO sessions(token_hash, created_at, expires_at, last_seen, user_agent, ip) VALUES (?,?,?,?,?,?)",
            (_token_hash(token), ts, expires, ts, (user_agent or "")[:200], ip or ""),
        )
    return token, expires


def check_session(token: str | None) -> dict | None:
    if not token:
        return None
    h = _token_hash(token)
    row = db.q1("SELECT * FROM sessions WHERE token_hash = ?", (h,))
    ts = now_ms()
    if not row:
        return None
    if row["expires_at"] < ts:
        with db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (h,))
        return None
    if ts - row["last_seen"] > 60_000:
        # Sliding expiry: active devices stay signed in.
        with db.tx() as c:
            c.execute(
                "UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
                (ts, ts + SESSION_DAYS * 86400 * 1000, h),
            )
    row["id"] = h[:12]
    return row


def end_session(token: str | None) -> None:
    if token:
        with db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def end_other_sessions(keep_token: str | None) -> int:
    keep = _token_hash(keep_token) if keep_token else ""
    with db.tx() as c:
        cur = c.execute("DELETE FROM sessions WHERE token_hash != ?", (keep,))
        return cur.rowcount


def list_sessions(current_token: str | None) -> list[dict]:
    cur = _token_hash(current_token) if current_token else ""
    rows = db.q("SELECT token_hash, created_at, last_seen, user_agent, ip FROM sessions ORDER BY last_seen DESC")
    return [
        {
            "id": r["token_hash"][:12],
            "created_at": r["created_at"],
            "last_seen": r["last_seen"],
            "user_agent": r["user_agent"],
            "ip": r["ip"],
            "current": r["token_hash"] == cur,
        }
        for r in rows
    ]


def purge_expired_sessions() -> None:
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (now_ms(),))


# ---------------------------------------------------------------------------
# First-run setup code & login throttling
# ---------------------------------------------------------------------------
_setup_code: str | None = None


def setup_code() -> str:
    global _setup_code
    if _setup_code is None:
        _setup_code = f"{secrets.randbelow(1000):03d}-{secrets.randbelow(1000):03d}"
    return _setup_code


class Throttle:
    """Exponential back-off per client after repeated failed sign-ins."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fails: dict[str, tuple[int, float]] = {}

    def wait_seconds(self, key: str) -> int:
        with self._lock:
            n, until = self._fails.get(key, (0, 0.0))
            return max(0, int(until - time.time() + 0.999))

    def fail(self, key: str) -> None:
        with self._lock:
            n, _ = self._fails.get(key, (0, 0.0))
            n += 1
            delay = 0 if n < 5 else min(900, 2 ** (n - 4))
            self._fails[key] = (n, time.time() + delay)

    def ok(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


throttle = Throttle()


# ---------------------------------------------------------------------------
# Network classification
# ---------------------------------------------------------------------------
_TAILSCALE = ipaddress.ip_network("100.64.0.0/10")


def classify_ip(ip: str) -> str:
    """'loopback' | 'tailscale' | 'lan' | 'public' | 'unknown'"""
    try:
        addr = ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError:
        return "unknown"
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if addr.is_loopback:
        return "loopback"
    if isinstance(addr, ipaddress.IPv4Address) and addr in _TAILSCALE:
        return "tailscale"
    if isinstance(addr, ipaddress.IPv6Address) and str(addr).lower().startswith("fd7a:115c:a1e0"):
        return "tailscale"
    if addr.is_private or addr.is_link_local:
        return "lan"
    return "public"


def is_local_address(ip: str) -> bool:
    return classify_ip(ip) in ("loopback", "tailscale", "lan")


# ---------------------------------------------------------------------------
# Encryption (locked notes, encrypted backups)
# ---------------------------------------------------------------------------
_MAGIC = b"DBK1"
_ENC_SCRYPT = {"n": 2**15, "r": 8, "p": 1}


def encrypt_bytes(data: bytes, passphrase: str) -> bytes:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _scrypt(passphrase, salt, **_ENC_SCRYPT)
    return _MAGIC + salt + nonce + AESGCM(key).encrypt(nonce, data, _MAGIC)


def decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
    if len(blob) < 4 + 16 + 12 + 16 or blob[:4] != _MAGIC:
        raise ValueError("Not a Daybook encrypted file.")
    salt, nonce, ct = blob[4:20], blob[20:32], blob[32:]
    key = _scrypt(passphrase, salt, **_ENC_SCRYPT)
    try:
        return AESGCM(key).decrypt(nonce, ct, _MAGIC)
    except Exception as exc:  # InvalidTag
        raise ValueError("Wrong passphrase, or the file is damaged.") from exc


def encrypt_text(text: str, passphrase: str) -> str:
    return _b64(encrypt_bytes(text.encode("utf-8"), passphrase))


def decrypt_text(token: str, passphrase: str) -> str:
    return decrypt_bytes(_unb64(token), passphrase).decode("utf-8")


# Vault passphrase (one passphrase for all locked notes) ---------------------
def vault_is_set() -> bool:
    return bool(db.get_settings()["vault"].get("verifier"))


def set_vault(passphrase: str) -> None:
    db.update_settings({"vault": {"verifier": hash_passphrase(passphrase)}})


def check_vault(passphrase: str) -> bool:
    verifier = db.get_settings()["vault"].get("verifier")
    return bool(verifier) and verify_passphrase(passphrase or "", verifier)

"""Paths and runtime configuration.

Data lives outside the repository by default (and outside iCloud-synced
folders on macOS) so notes are never committed or uploaded by accident:

* macOS:  ~/Library/Application Support/Daybook
* Linux:  $XDG_DATA_HOME/daybook  (~/.local/share/daybook)

Override with the DAYBOOK_DATA environment variable.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def _default_data_dir() -> Path:
    env = os.environ.get("DAYBOOK_DATA")
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Daybook"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "daybook"


class Paths:
    def __init__(self, root: Path | None = None) -> None:
        self.data = (root or _default_data_dir()).expanduser()
        self.db = self.data / "daybook.db"
        self.attachments = self.data / "attachments"
        self.backups = self.data / "backups"
        self.tmp = self.data / "tmp"
        self.cache = self.data / "cache"          # derived files (rendered slides); safe to delete

    def ensure(self) -> None:
        for p in (self.data, self.attachments, self.backups, self.tmp, self.cache):
            p.mkdir(parents=True, exist_ok=True)
        # Owner-only permissions on the data folder.
        try:
            os.chmod(self.data, 0o700)
        except OSError:
            pass


paths = Paths()


def local_timezone_name() -> str:
    """Best-effort IANA name of the machine's timezone."""
    tz = os.environ.get("TZ")
    if tz and "/" in tz:
        return tz
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    # Fall back to a fixed offset-free guess.
    return "America/New_York" if time.timezone == 18000 else "UTC"


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
MAX_UPLOAD_MB = int(os.environ.get("DAYBOOK_MAX_UPLOAD_MB", "250"))

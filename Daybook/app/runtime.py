"""Handles to the running server, so Daybook can restart itself after an update.

run.py fills these in. Under tests, or when something else serves the app, `server`
stays None and Daybook reports that it can't restart itself.
"""
from __future__ import annotations

server = None               # the uvicorn Server started by run.py
restart_requested = False   # run.py starts the new copy when the server stops with this set

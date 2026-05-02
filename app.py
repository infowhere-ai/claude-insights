"""Entry point shim — exposes `app` for `uvicorn app:app`."""
from claude_monitor.main import app  # noqa: F401

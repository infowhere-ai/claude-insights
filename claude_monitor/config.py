"""Application configuration — read from environment at module load time."""
import datetime
import os
import subprocess
from pathlib import Path


def _default_projects_root() -> str:
    try:
        result = subprocess.run(
            ["git", "worktree", "list"],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            first_line = result.stdout.splitlines()[0]
            main_worktree = first_line.split()[0]
            return str(Path(main_worktree).parent)
    except Exception:
        pass
    return str(Path(__file__).parent.parent.parent)


PROJECTS_ROOT = Path(os.getenv("PROJECTS_ROOT", _default_projects_root()))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "0.5"))
DISCOVERY_INTERVAL = float(os.getenv("DISCOVERY_INTERVAL", "60.0"))
JSONL_ACTIVE_SECONDS = float(os.getenv("JSONL_ACTIVE_SECONDS", "60.0"))
CLAUDE_PROJECTS_DIR = Path(os.getenv("CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects")))

VERSION = "1.0.0"
BUILD_DATE = os.getenv("BUILD_DATE", datetime.date.today().isoformat())

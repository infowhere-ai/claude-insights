"""Application configuration — read from environment at module load time."""

import datetime
import importlib.metadata
import os
import subprocess
from pathlib import Path


def _default_projects_root() -> str:
    try:
        result = subprocess.run(
            ["git", "worktree", "list"],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True,
            text=True,
            timeout=5,
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

# Restrict CORS to localhost by default — override with CORS_ORIGINS env var (comma-separated)
CORS_ORIGIN_REGEX: str = os.getenv(
    "CORS_ORIGIN_REGEX",
    r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
)

CLAUDE_HOME = Path(os.getenv("CLAUDE_HOME", str(Path.home() / ".claude")))
CLAUDE_PROJECTS_DIR = Path(os.getenv("CLAUDE_PROJECTS_DIR", str(CLAUDE_HOME / "projects")))
CLAUDE_SETTINGS_FILE = CLAUDE_HOME / "settings.json"
CLAUDE_STATS_CACHE = CLAUDE_HOME / "stats-cache.json"
CLAUDE_SKILLS_DIR = CLAUDE_HOME / "skills"
CLAUDE_RULES_DIR = CLAUDE_HOME / "rules"
CLAUDE_GLOBAL_MD = CLAUDE_HOME / "CLAUDE.md"

try:
    VERSION = importlib.metadata.version("claude-insights")
except importlib.metadata.PackageNotFoundError:
    VERSION = "dev"
BUILD_DATE = os.getenv("BUILD_DATE", datetime.date.today().isoformat())

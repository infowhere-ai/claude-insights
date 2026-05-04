"""Project discovery and status reading."""

import json
from pathlib import Path

from claude_monitor import config, state


def read_status(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _collect_root(root: Path, candidates: dict[str, Path], pending: set[str]) -> None:
    """Scan a single root directory for projects with .claude/status.json.

    Populates candidates (name→status_path) and pending (names with .claude
    but no status.json).
    """
    for status_path in root.glob("*/.claude/status.json"):
        name = status_path.parts[-3]
        if name not in candidates:
            candidates[name] = status_path
    try:
        for subdir in root.iterdir():
            if not subdir.is_dir():
                continue
            claude_dir = subdir / ".claude"
            if claude_dir.is_dir() and not (claude_dir / "status.json").exists():
                pending.add(subdir.name)
    except OSError:
        pass


def discover() -> None:
    """Discovers projects with .claude/status.json under PROJECTS_ROOT and extra roots."""
    candidates: dict[str, Path] = {}
    pending: set[str] = set()

    _collect_root(config.PROJECTS_ROOT, candidates, pending)
    for root in state._extra_roots:
        _collect_root(root, candidates, pending)

    found: set[str] = set()
    project_dirs: set[Path] = {sp.parent.parent for sp in candidates.values()}

    for name, status_path in candidates.items():
        project_dir = status_path.parent.parent
        if project_dir.parent in project_dirs:
            continue
        found.add(name)
        if name not in state._status_paths:
            state._status_paths[name] = status_path

    gone = set(state._status_paths.keys()) - found
    for name in gone:
        state._status_paths.pop(name, None)
        state.projects.pop(name, None)
        state._mtimes.pop(name, None)

    state._pending_projects = sorted(pending - found)

"""Git integration endpoints — diff and pending (uncommitted) files."""

import asyncio
import subprocess
from pathlib import Path
from subprocess import CompletedProcess

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from claude_monitor import config, state

router = APIRouter(tags=["git"])

_STATUS_LABELS = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "U": "unmerged",
    "?": "untracked",
    "!": "ignored",
}


async def _git_run(cmd: list[str], cwd: Path, timeout: int) -> CompletedProcess:
    """Run a git command in a thread pool. Returns CompletedProcess."""
    return await asyncio.to_thread(  # nosonar
        subprocess.run,
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


async def _diff_head(project_path: Path, file_path: Path) -> str:
    """Return diff of file vs HEAD, or empty string."""
    result = await _git_run(["git", "diff", "HEAD", "--", str(file_path)], project_path, 10)
    return result.stdout.strip()


async def _diff_staged(project_path: Path, file_path: Path) -> str:
    """Return diff of staged changes for file, or empty string."""
    result = await _git_run(["git", "diff", "--cached", "--", str(file_path)], project_path, 10)
    return result.stdout.strip()


async def _diff_untracked(project_path: Path, file_path: Path) -> tuple[str, bool]:
    """Check if file is untracked and return (diff, is_untracked)."""
    ls = await _git_run(["git", "ls-files", "--error-unmatch", str(file_path)], project_path, 5)
    if ls.returncode != 0:
        result = await _git_run(
            ["git", "diff", "--no-index", "/dev/null", str(file_path)], project_path, 10
        )
        return result.stdout.strip(), True
    return "", False


def _resolve_pending_project_path(project: str) -> Path | None:
    """Resolve the filesystem path for a project name.

    Checks state._status_paths first, then PROJECTS_ROOT and extra roots.
    Returns None when the project cannot be found.
    """
    if project in state._status_paths:
        return state._status_paths[project].parents[1]
    for candidate in [config.PROJECTS_ROOT / project] + [r / project for r in state._extra_roots]:
        if candidate.is_dir():
            return candidate
    return None


def _parse_porcelain_line(line: str, project_path: Path) -> dict | None:
    """Parse a single line of `git status --porcelain` output.

    Returns a file-info dict or None if the line is blank.
    """
    if not line.strip():
        return None
    xy = line[:2]
    rel = line[3:]
    if " -> " in rel:
        rel = rel.split(" -> ", 1)[1]
    rel = rel.strip().strip('"')
    code = xy[0].strip() or xy[1].strip() or "?"
    return {
        "path": str(project_path / rel),
        "rel_path": rel,
        "status_code": code,
        "label": _STATUS_LABELS.get(code, "changed"),
    }


@router.get("/api/pending")
async def get_pending_files(project: str = Query(...)):
    project_path = _resolve_pending_project_path(project)

    if project_path is None or not project_path.is_dir():
        return JSONResponse({"error": "project not found"}, status_code=404)

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "status", "--porcelain"],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return JSONResponse({"files": [], "error": result.stderr.strip()})

        files = []
        for line in result.stdout.splitlines():
            entry = _parse_porcelain_line(line, project_path)
            if entry is not None:
                files.append(entry)

        return {"files": files, "project_path": str(project_path)}
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "timeout"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/diff")
async def get_diff(project: str = Query(...), file: str = Query(...)):
    project_path = config.PROJECTS_ROOT / project
    if not project_path.is_dir():
        return JSONResponse({"error": "project not found"}, status_code=404)

    file_path = Path(file)
    if not file_path.is_absolute():
        file_path = project_path / file_path

    if not file_path.is_file():
        return JSONResponse({"error": "file not found", "diff": ""})

    try:
        diff = await _diff_head(project_path, file_path)
        if not diff:
            diff = await _diff_staged(project_path, file_path)
        if not diff:
            diff, is_untracked = await _diff_untracked(project_path, file_path)
        else:
            is_untracked = False
        return JSONResponse({"diff": diff, "file": str(file_path), "is_new": is_untracked})
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "timeout"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

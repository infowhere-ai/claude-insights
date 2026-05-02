"""File operations endpoints — browse, preview, delete."""
import subprocess
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from claude_monitor import config, state

router = APIRouter(tags=["files"])


@router.get("/api/browse")
async def browse_directory(path: str = Query(default="")):
    target = Path(path).expanduser().resolve() if path else Path.home()
    if not target.is_dir():
        return JSONResponse({"error": "not a directory"}, status_code=400)
    try:
        dirs = sorted(
            [str(p) for p in target.iterdir() if p.is_dir() and not p.name.startswith(".")],
            key=lambda s: s.lower(),
        )
    except PermissionError:
        return JSONResponse({"error": "permission denied"}, status_code=403)
    parent = str(target.parent) if target.parent != target else None
    return {"current": str(target), "parent": parent, "dirs": dirs}


@router.delete("/api/file")
async def delete_file(project: str = Query(...), path: str = Query(...)):
    project_path: Path | None = None
    if project in state._status_paths:
        project_path = state._status_paths[project].parent.parent
    else:
        for root in [config.PROJECTS_ROOT] + list(state._extra_roots):
            candidate = root / project
            if candidate.is_dir() and (candidate / ".claude").is_dir():
                project_path = candidate
                break

    if project_path is None or not project_path.is_dir():
        return JSONResponse({"error": "project not found"}, status_code=404)

    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = project_path / file_path
    file_path = file_path.resolve()

    try:
        file_path.relative_to(project_path.resolve())
    except ValueError:
        return JSONResponse({"error": "path outside project"}, status_code=400)

    if not file_path.exists():
        return JSONResponse({"error": "file not found"}, status_code=404)

    if not file_path.is_file():
        return JSONResponse({"error": "path is not a file"}, status_code=400)

    try:
        ls = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(file_path)],
            cwd=str(project_path),
            capture_output=True, text=True, timeout=5,
        )
        if ls.returncode == 0:
            return JSONResponse(
                {"error": "file is tracked by git — only untracked files can be deleted here"},
                status_code=400,
            )
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "timeout checking git status"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    try:
        file_path.unlink()
        return {"deleted": str(file_path)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/file-preview")
async def get_file_preview(path: str = Query(...)):
    MAX_CHARS = 50_000
    fp = Path(path)
    if not fp.suffix == ".md":
        return JSONResponse({"error": "only .md files allowed"}, status_code=400)
    if not fp.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)
    try:
        content = fp.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    truncated = len(content) > MAX_CHARS
    return {
        "content": content[:MAX_CHARS],
        "total": len(content),
        "shown": min(len(content), MAX_CHARS),
        "truncated": truncated,
    }

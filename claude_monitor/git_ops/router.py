"""Git integration endpoints — diff and pending (uncommitted) files."""
import subprocess
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from claude_monitor import config, state

router = APIRouter(tags=["git"])


@router.get("/api/pending")
async def get_pending_files(project: str = Query(...)):
    project_path: Path | None = None
    if project in state._status_paths:
        project_path = state._status_paths[project].parents[1]
    else:
        for candidate in [config.PROJECTS_ROOT / project] + [r / project for r in state._extra_roots]:
            if candidate.is_dir():
                project_path = candidate
                break

    if project_path is None or not project_path.is_dir():
        return JSONResponse({"error": "project not found"}, status_code=404)

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_path),
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return JSONResponse({"files": [], "error": result.stderr.strip()})

        STATUS_LABELS = {
            "M": "modified", "A": "added", "D": "deleted",
            "R": "renamed", "C": "copied", "U": "unmerged",
            "?": "untracked", "!": "ignored",
        }

        files = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            xy = line[:2]
            rel = line[3:]
            if " -> " in rel:
                rel = rel.split(" -> ", 1)[1]
            rel = rel.strip().strip('"')
            code = xy[0].strip() or xy[1].strip() or "?"
            abs_path = str(project_path / rel)
            files.append({
                "path": abs_path,
                "rel_path": rel,
                "status_code": code,
                "label": STATUS_LABELS.get(code, "changed"),
            })

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
        result = subprocess.run(
            ["git", "diff", "HEAD", "--", str(file_path)],
            cwd=str(project_path),
            capture_output=True, text=True, timeout=10,
        )
        diff = result.stdout.strip()

        if not diff:
            result2 = subprocess.run(
                ["git", "diff", "--cached", "--", str(file_path)],
                cwd=str(project_path),
                capture_output=True, text=True, timeout=10,
            )
            diff = result2.stdout.strip()

        is_untracked = False
        if not diff:
            ls_result = subprocess.run(
                ["git", "ls-files", "--error-unmatch", str(file_path)],
                cwd=str(project_path),
                capture_output=True, text=True, timeout=5,
            )
            is_untracked = ls_result.returncode != 0
            if is_untracked:
                result3 = subprocess.run(
                    ["git", "diff", "--no-index", "/dev/null", str(file_path)],
                    cwd=str(project_path),
                    capture_output=True, text=True, timeout=10,
                )
                diff = result3.stdout.strip()

        return JSONResponse({"diff": diff, "file": str(file_path), "is_new": is_untracked})
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "timeout"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

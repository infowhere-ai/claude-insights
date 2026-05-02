"""Configuration endpoints — roots, CLAUDE.md."""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from claude_monitor import config, state
from claude_monitor.app_config import service
from claude_monitor.projects import service as project_service

router = APIRouter(tags=["config"])


@router.get("/api/config")
async def get_config():
    return {
        "primary_root": str(config.PROJECTS_ROOT),
        "extra_roots": [str(p) for p in state._extra_roots],
    }


@router.post("/api/config/roots")
async def update_roots(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    action = data.get("action", "")
    path_str = (data.get("path") or "").strip()
    if not path_str:
        return JSONResponse({"error": "path is required"}, status_code=400)

    p = Path(path_str).expanduser().resolve()

    if action == "add":
        if not p.is_dir():
            return JSONResponse({"error": f"Directory not found: {p}"}, status_code=400)
        if p == config.PROJECTS_ROOT:
            return JSONResponse({"error": "This folder is already the primary folder"}, status_code=400)
        if p not in state._extra_roots:
            state._extra_roots.append(p)
            service.save_roots_config()
            project_service.discover()
    elif action == "remove":
        state._extra_roots = [r for r in state._extra_roots if r != p]
        service.save_roots_config()
        project_service.discover()
    else:
        return JSONResponse({"error": "action must be 'add' or 'remove'"}, status_code=400)

    return {
        "primary_root": str(config.PROJECTS_ROOT),
        "extra_roots": [str(r) for r in state._extra_roots],
    }


@router.get("/api/claude-md")
async def get_claude_md(project: str):
    project_path = config.PROJECTS_ROOT / project
    if not project_path.is_dir():
        for root in state._extra_roots:
            candidate = root / project
            if candidate.is_dir():
                project_path = candidate
                break
        else:
            return JSONResponse({"error": "project not found"}, status_code=404)

    for candidate in [project_path / "CLAUDE.md", project_path / ".claude" / "CLAUDE.md"]:
        if candidate.is_file():
            try:
                content = candidate.read_text(encoding="utf-8")
                return JSONResponse({"content": content, "path": str(candidate.relative_to(project_path))})
            except Exception as e:
                return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({"content": None, "path": None})

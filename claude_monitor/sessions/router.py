"""Session and agent history endpoints."""

from claude_monitor import db
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from claude_monitor import config, state
from claude_monitor.jsonl import parser
from claude_monitor.sessions import service

router = APIRouter(tags=["sessions"])


@router.get("/api/sessions")
async def get_sessions(project: str = Query(...)):
    if project not in state._status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    return service.list_sessions(project)


@router.get("/api/session-detail")
async def get_session_detail(project: str = Query(...), session_id: str = Query(...)):
    if project not in state._status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    project_path = state._status_paths[project].parents[1]
    encoded = str(project_path).replace("/", "-")
    jsonl_path = config.CLAUDE_PROJECTS_DIR / encoded / f"{session_id}.jsonl"
    if not jsonl_path.is_file():
        return JSONResponse({"error": "session not found"}, status_code=404)
    return parser.parse_session_detail(jsonl_path)


@router.get("/api/agent-history")
async def get_agent_history(project: str = Query(None), limit: int = Query(100)):
    rows = db.get_agent_history(project=project, limit=limit)
    return {"agents": rows}


@router.get("/api/session-history")
async def get_session_history(project: str = Query(None), limit: int = Query(50)):
    rows = db.get_session_history(project=project, limit=limit)
    return {"sessions": rows}

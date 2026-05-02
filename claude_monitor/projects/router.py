"""Project status endpoints."""
from fastapi import APIRouter

from claude_monitor import state

router = APIRouter(tags=["projects"])


@router.get("/api/status")
async def get_status():
    return {"projects": state.projects, "connected_clients": len(state._sse_clients)}

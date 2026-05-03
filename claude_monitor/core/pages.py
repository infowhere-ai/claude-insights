"""Basic page endpoints — health, root redirect, insights, version."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

from claude_monitor import config, state

router = APIRouter(tags=["pages"])

_STATIC_HTML = Path(__file__).parent.parent.parent / "static" / "insights.html"


@router.get("/health")
async def health():
    return {"status": "ok", "projects_monitored": len(state.projects)}


@router.get("/")
async def root():
    return RedirectResponse(url="/insights")


@router.get("/insights")
async def insights_page():
    return FileResponse(str(_STATIC_HTML))


@router.get("/api/version")
async def get_version():
    return {"version": config.VERSION, "build_date": config.BUILD_DATE}

"""FastAPI application entry point."""
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from claude_monitor import db
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from claude_monitor import config, state
from claude_monitor.account.router import router as account_router
from claude_monitor.app_config.router import router as app_config_router
from claude_monitor.app_config import service as config_service
from claude_monitor.context.router import router as context_router
from claude_monitor.core import background
from claude_monitor.core.pages import router as pages_router
from claude_monitor.core.sse import router as sse_router
from claude_monitor.files.router import router as files_router
from claude_monitor.git_ops.router import router as git_router
from claude_monitor.projects import service as project_service
from claude_monitor.projects.router import router as projects_router
from claude_monitor.sessions.router import router as sessions_router
from claude_monitor.skills.router import router as skills_router
from claude_monitor.stats import service as stats_service
from claude_monitor.stats.router import router as stats_router
from claude_monitor.terminal.ws import router as terminal_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    from claude_monitor.sessions import service as session_service
    db.init_db()
    config_service.load_roots_config()
    project_service.discover()
    now_ts = time.time()
    for name, path in state._status_paths.items():
        data = project_service.read_status(path)
        if data is None:
            continue
        project_path = path.parents[1]
        agents_dir = project_path / ".claude" / "agents"
        active_agents = (
            session_service.persist_done_agents(
                agents_dir, name, session_service.current_session_id(name), now_ts
            ) if agents_dir.is_dir() else []
        )
        data["active_agents"] = active_agents
        data["events"] = list(state._project_events.get(name, []))
        hook_stats = data.get("stats") or {}
        data["stats"] = {**stats_service.get_project_stats(path.parents[1], name), **hook_stats}
        state.projects[name] = data
        state._mtimes[str(path)] = path.stat().st_mtime
    asyncio.create_task(background.discovery_loop())
    asyncio.create_task(background.poll_loop())
    asyncio.create_task(background.jsonl_watcher_loop())
    yield


app = FastAPI(title="claude-insights", version=config.VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["null"],                    # file:// protocol
    allow_origin_regex=config.CORS_ORIGIN_REGEX,  # localhost by default
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

app.include_router(pages_router)
app.include_router(projects_router)
app.include_router(sessions_router)
app.include_router(stats_router)
app.include_router(git_router)
app.include_router(skills_router)
app.include_router(files_router)
app.include_router(app_config_router)
app.include_router(account_router)
app.include_router(context_router)
app.include_router(terminal_router)
app.include_router(sse_router)



_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")

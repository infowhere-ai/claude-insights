import asyncio
import datetime
import fcntl
import json
import os
import pty
import shutil
import struct
import subprocess
import termios
import time
from pathlib import Path

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

PROJECTS_ROOT = Path(os.getenv("PROJECTS_ROOT", str(Path(__file__).parent.parent)))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "0.5"))
DISCOVERY_INTERVAL = float(os.getenv("DISCOVERY_INTERVAL", "60.0"))
JSONL_ACTIVE_SECONDS = float(os.getenv("JSONL_ACTIVE_SECONDS", "60.0"))  # session considered active if JSONL changed within this window
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

VERSION = "1.0.0"
BUILD_DATE = os.getenv("BUILD_DATE", datetime.date.today().isoformat())

app = FastAPI(title="claude-monitor", version=VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# In-memory state
projects: dict[str, dict] = {}         # project_name -> status dict
_status_paths: dict[str, Path] = {}    # project_name -> path to status.json
_mtimes: dict[str, float] = {}         # path_str -> mtime
_sse_clients: list[asyncio.Queue] = [] # one queue per SSE client
_pending_projects: list[str] = []      # projects with .claude/ but no status.json

# Per-project event log (rolling, max 500 entries)
_project_events: dict[str, list] = {}

# Per-project JSONL stats cache (token counts — reads full file)
_jsonl_mtimes: dict[str, float] = {}   # jsonl_path -> mtime
_project_stats_cache: dict[str, dict] = {}  # project_name -> stats

# JSONL watcher cache (state detection — reads only the tail)
_jsonl_cache: dict[str, dict] = {}  # project_name -> {mtime, tool, jsonl_path}

# Config: extra roots (beyond PROJECTS_ROOT)
_CONFIG_FILE = PROJECTS_ROOT / ".claude" / "monitor-roots.json"
_extra_roots: list[Path] = []


# ── JSONL helpers ─────────────────────────────────────────────────────────────

def _get_jsonl_dir(project_path: Path) -> Path:
    """Returns ~/.claude/projects/<encoded> for this project path."""
    encoded = str(project_path).replace("/", "-")
    return CLAUDE_PROJECTS_DIR / encoded


def _get_latest_jsonl(project_path: Path) -> tuple[Path | None, float]:
    """Returns (path, mtime) of the most recently modified .jsonl in the project's session dir."""
    jsonl_dir = _get_jsonl_dir(project_path)
    if not jsonl_dir.is_dir():
        return None, 0.0
    try:
        files = list(jsonl_dir.glob("*.jsonl"))
        if not files:
            return None, 0.0
        latest = max(files, key=lambda p: p.stat().st_mtime)
        return latest, latest.stat().st_mtime
    except OSError:
        return None, 0.0


def _parse_jsonl_tail(jsonl_path: Path) -> dict:
    """Read last 8 KB of a JSONL session file. Returns {tool, cwd}.
    - tool: name of the most recent tool_use in an assistant message
    - cwd:  working directory recorded in any recent entry
    """
    try:
        with open(jsonl_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            raw = fh.read().decode("utf-8", errors="replace")
        tool: str | None = None
        cwd: str | None = None
        for line in reversed(raw.strip().split("\n")):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not cwd and d.get("cwd"):
                cwd = d["cwd"]
            if tool is None and d.get("type") == "assistant":
                content = d.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for c in reversed(content):
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            tool = c.get("name", "Tool")
                            break
            if cwd and tool:
                break
        return {"tool": tool, "cwd": cwd}
    except Exception:
        return {}


def _load_roots_config() -> None:
    global _extra_roots
    try:
        if _CONFIG_FILE.exists():
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            _extra_roots = [Path(p) for p in data.get("extra_roots", []) if Path(p).is_dir()]
    except Exception:
        _extra_roots = []


def _save_roots_config() -> None:
    try:
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(
            json.dumps({"extra_roots": [str(p) for p in _extra_roots]}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _discover() -> None:
    """Discovers projects with .claude/status.json under PROJECTS_ROOT and extra roots."""
    global _pending_projects
    found: set[str] = set()
    pending: set[str] = set()

    # Two-pass discovery: first collect all candidates, then filter subprojects.
    # A project is a subproject if its parent directory is itself a discovered
    # project directory (e.g. project-finances/backend where project-finances
    # is also discovered). This works regardless of which root triggered discovery.
    candidates: dict[str, Path] = {}  # name -> status_path

    def _collect_root(root: Path) -> None:
        for status_path in root.glob("*/.claude/status.json"):
            name = status_path.parts[-3]
            if name not in candidates:
                candidates[name] = status_path
        # Also scan for dirs with .claude/ but no status.json
        try:
            for subdir in root.iterdir():
                if not subdir.is_dir():
                    continue
                claude_dir = subdir / ".claude"
                if claude_dir.is_dir() and not (claude_dir / "status.json").exists():
                    pending.add(subdir.name)
        except OSError:
            pass

    _collect_root(PROJECTS_ROOT)
    for root in _extra_roots:
        _collect_root(root)

    # Build set of all discovered project directories for the subproject check.
    project_dirs: set[Path] = {sp.parent.parent for sp in candidates.values()}

    for name, status_path in candidates.items():
        project_dir = status_path.parent.parent
        # Skip if this project's parent dir is itself a discovered project.
        if project_dir.parent in project_dirs:
            continue
        found.add(name)
        if name not in _status_paths:
            _status_paths[name] = status_path

    # Remove projects whose status file has disappeared
    gone = set(_status_paths.keys()) - found
    for name in gone:
        _status_paths.pop(name, None)
        projects.pop(name, None)
        _mtimes.pop(name, None)

    # pending = has .claude/ but no active status.json (and not already active)
    _pending_projects = sorted(pending - found)


def _read_status(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _broadcast(data: dict) -> None:
    for q in _sse_clients:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass


def _get_project_stats(project_path: Path, project_name: str) -> dict:
    """Reads token usage + model from the most recent JSONL session file for this project."""
    encoded = str(project_path).replace("/", "-")
    jsonl_dir = Path.home() / ".claude" / "projects" / encoded
    if not jsonl_dir.is_dir():
        return {}

    # Find most recently modified .jsonl file
    try:
        jsonl_files = list(jsonl_dir.glob("*.jsonl"))
    except OSError:
        return {}
    if not jsonl_files:
        return {}

    latest = max(jsonl_files, key=lambda p: p.stat().st_mtime)
    latest_mtime = latest.stat().st_mtime
    cache_key = str(latest)

    if _jsonl_mtimes.get(cache_key) == latest_mtime and project_name in _project_stats_cache:
        return _project_stats_cache[project_name]

    # Parse the JSONL
    session_input = 0
    session_output = 0
    session_cache_read = 0
    last_input_tokens = 0   # input_tokens of the LAST assistant message = current ctx window size
    model = ""
    try:
        with latest.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") == "assistant" and "message" in d:
                    u = d["message"].get("usage", {})
                    session_input += u.get("input_tokens", 0)
                    session_output += u.get("output_tokens", 0)
                    session_cache_read += u.get("cache_read_input_tokens", 0)
                    # True context window size = uncached + cached_read + cache_creation
                    # (input_tokens alone is 1 when everything is cached)
                    last_input_tokens = (
                        u.get("input_tokens", 0)
                        + u.get("cache_read_input_tokens", 0)
                        + u.get("cache_creation_input_tokens", 0)
                    )
                    m = d["message"].get("model", "")
                    if m:
                        model = m
    except OSError:
        return {}

    stats = {
        "session_input_tokens": session_input,
        "session_output_tokens": session_output,
        "session_cache_read": session_cache_read,
        # last_input_tokens = input_tokens of the most recent API call = current context window usage
        # (not the cumulative sum — each call reports its own context size)
        "session_ctx_tokens": last_input_tokens,
        "model": model,
    }
    _jsonl_mtimes[cache_key] = latest_mtime
    _project_stats_cache[project_name] = stats
    return stats


async def discovery_loop() -> None:
    while True:
        _discover()
        await asyncio.sleep(DISCOVERY_INTERVAL)


async def poll_loop() -> None:
    # Initial wait to let discovery run first
    await asyncio.sleep(2)
    while True:
        now_ts = time.time()
        for name, path in list(_status_paths.items()):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            path_str = str(path)
            if _mtimes.get(path_str) != mtime:
                _mtimes[path_str] = mtime
                data = _read_status(path)
                if data is not None:
                    # If JSONL is still fresh, keep WORKING state regardless of what
                    # status.json says. This prevents a PostToolUse hook (or a broken hook)
                    # from flipping us to idle while Claude is still active.
                    jsonl_info = _jsonl_cache.get(name, {})
                    jsonl_mtime = jsonl_info.get("mtime", 0.0)
                    # Only override status.json with JSONL if JSONL is newer than
                    # status.json — meaning the hooks didn't capture this event.
                    # If status.json is newer (e.g. Stop hook wrote idle), trust it.
                    if jsonl_mtime and jsonl_mtime > mtime and (now_ts - jsonl_mtime) <= JSONL_ACTIVE_SECONDS:
                        data["state"] = "working"
                        data["status"] = "working"
                        # Only clear a notification if JSONL is substantially newer (>2s).
                        # A tiny margin (<2s) is just Claude Code writing "system" entries
                        # immediately after firing the Notification hook — race condition.
                        if (jsonl_mtime - mtime) > 2.0:
                            data["notification"] = None
                        jsonl_tool = jsonl_info.get("tool")
                        if jsonl_tool:
                            data["current_action"] = {
                                "hook": "PreToolUse",
                                "tool": jsonl_tool,
                                "description": jsonl_tool,
                            }
                            data["tool"] = jsonl_tool
                    # Load active agents
                    active_agents = []
                    project_path = path.parents[1]
                    agents_dir = project_path / ".claude" / "agents"
                    if agents_dir.is_dir():
                        agents_now = time.time()
                        for agent_file in agents_dir.glob("*.json"):
                            try:
                                agent_data = json.loads(agent_file.read_text(encoding="utf-8"))
                                # Include running agents + recently done agents (last 5 min)
                                if agent_data.get("state") == "running":
                                    active_agents.append(agent_data)
                                elif agent_data.get("state") == "done":
                                    finished_at = agent_data.get("finished_at", "")
                                    if finished_at:
                                        try:
                                            from datetime import datetime
                                            ft = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
                                            age = agents_now - ft.timestamp()
                                            if age < 300:  # 5 minutes
                                                active_agents.append(agent_data)
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                        active_agents.sort(key=lambda a: a.get("started_at", ""))
                    data["active_agents"] = active_agents

                    # If any sub-agent is running, parent is working regardless of status.json
                    # (parent JSONL goes stale while agents run in their own JSONL files)
                    if any(a.get("state") == "running" for a in active_agents):
                        data["state"] = "working"
                        data["status"] = "working"
                        data["notification"] = None

                    # Accumulate event log
                    event = {
                        "timestamp": data.get("ts", datetime.datetime.utcnow().isoformat() + "Z"),
                        "status": data.get("status", "idle"),
                        "tool": data.get("tool"),
                        "message": data.get("tool") if data.get("status") == "working" else data.get("status", "idle"),
                        "hook": "PreToolUse" if data.get("status") == "working" else "PostToolUse",
                    }
                    events = _project_events.setdefault(name, [])
                    events.append(event)
                    if len(events) > 500:
                        events[:] = events[-500:]
                    data["events"] = list(events)

                    # Inject per-project token stats
                    # Merge: JSONL provides base/fallback; hook stats (from status.json)
                    # override with fresher real-time values (e.g. session_ctx_tokens
                    # written by PreToolUse/Stop hooks from the CURRENT session's transcript).
                    project_path = _status_paths[name].parents[1]
                    hook_stats = data.get("stats") or {}
                    jsonl_stats = _get_project_stats(project_path, name)
                    data["stats"] = {**jsonl_stats, **hook_stats}

                    projects[name] = data
                    _broadcast({"type": "update", "project_name": name, "data": data, "pending_projects": _pending_projects})

        await asyncio.sleep(POLL_INTERVAL)


async def jsonl_watcher_loop() -> None:
    """Primary state detection engine: watches JSONL transcript files.

    Claude always writes to ~/.claude/projects/<encoded>/*.jsonl regardless of
    hook configuration.  mtime < JSONL_ACTIVE_SECONDS → WORKING; stale → IDLE.

    Pass 1: known projects mapped via _status_paths (fast).
    Pass 2: scan all JSONL dirs for active sessions not yet in _status_paths
            and trigger re-discovery if a new one is found under a known root.
    """
    await asyncio.sleep(3)  # let discovery run first
    while True:
        try:
            now_ts = time.time()

            # ── Pass 1: known projects ─────────────────────────────────────────
            for name, sp in list(_status_paths.items()):
                project_path = sp.parents[1]
                latest_jsonl, latest_mtime = _get_latest_jsonl(project_path)
                if latest_jsonl is None:
                    continue

                cached = _jsonl_cache.get(name, {})
                if cached.get("mtime") != latest_mtime:
                    parsed = _parse_jsonl_tail(latest_jsonl)
                    _jsonl_cache[name] = {
                        "mtime": latest_mtime,
                        "tool": parsed.get("tool"),
                        "jsonl_path": str(latest_jsonl),
                    }
                    cached = _jsonl_cache[name]

                age = now_ts - latest_mtime
                tool = cached.get("tool") or ""
                current = projects.get(name, {})
                cur_state = current.get("state") or current.get("status", "idle")

                # Read status.json mtime to decide if hooks already handled this
                status_mtime = 0.0
                try:
                    status_mtime = sp.stat().st_mtime
                except OSError:
                    pass

                if age <= JSONL_ACTIVE_SECONDS and latest_mtime > status_mtime:
                    # Session is active — ensure state is WORKING
                    cur_action = current.get("current_action")
                    cur_tool = cur_action.get("tool") if isinstance(cur_action, dict) else cur_action
                    # Broadcast if state/tool changed OR if JSONL has new data (stats refresh)
                    stats_stale = _jsonl_mtimes.get(str(latest_jsonl)) != latest_mtime
                    if cur_state != "working" or cur_tool != tool or stats_stale:
                        updated = dict(current)
                        updated["state"] = "working"
                        updated["status"] = "working"
                        # Only clear a notification if JSONL is substantially newer (>2s).
                        # A tiny margin is the race condition where Claude Code writes
                        # "system" entries right after the Notification hook fires.
                        if (latest_mtime - status_mtime) > 2.0:
                            updated["notification"] = None
                        if tool:
                            updated["current_action"] = {
                                "hook": "PreToolUse",
                                "tool": tool,
                                "description": tool,
                            }
                            updated["tool"] = tool
                        updated["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        # Refresh token stats whenever JSONL has new data.
                        # Order: hook_stats first, then jsonl_stats on top — so that JSONL
                        # token fields (session_ctx_tokens, session_input_tokens, etc.)
                        # override the stale values frozen in the last hook event.
                        # Non-token hook fields (sub_agents_total, agent_depth, week_*,
                        # compact_ctx_threshold) are preserved from hook_stats base.
                        if stats_stale:
                            hook_stats = updated.get("stats") or {}
                            jsonl_stats = _get_project_stats(project_path, name)
                            updated["stats"] = {**hook_stats, **jsonl_stats}
                        projects[name] = updated
                        _broadcast({"type": "update", "project_name": name, "data": updated, "pending_projects": _pending_projects})
                else:
                    # JSONL stale — if we still think it's working, flip to idle
                    # But don't flip if sub-agents are still running (their JSONLs are active,
                    # but the parent JSONL goes stale — parent state is driven by poll_loop)
                    has_running_agents = any(
                        a.get("state") == "running" for a in current.get("active_agents", [])
                    )
                    if cur_state == "working" and not has_running_agents:
                        stale = dict(current)
                        stale["status"] = "idle"
                        stale["state"] = "idle"
                        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        stale["ts"] = now_iso
                        stale["updated_at"] = now_iso
                        stale["message"] = "idle"
                        stale["_stale"] = True
                        projects[name] = stale
                        _broadcast({"type": "update", "project_name": name, "data": stale, "pending_projects": _pending_projects})
                    elif cur_state == "working" and has_running_agents:
                        # Sub-agents running: parent JSONL is stale but session is active.
                        # Still refresh stats periodically so ctx% stays current.
                        stats_stale = _jsonl_mtimes.get(str(latest_jsonl)) != latest_mtime
                        if stats_stale:
                            updated = dict(current)
                            hook_stats = updated.get("stats") or {}
                            jsonl_stats = _get_project_stats(project_path, name)
                            updated["stats"] = {**hook_stats, **jsonl_stats}
                            updated["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                            projects[name] = updated
                            _broadcast({"type": "update", "project_name": name, "data": updated, "pending_projects": _pending_projects})

            # ── Pass 2: discover sessions not yet tracked ──────────────────────
            if CLAUDE_PROJECTS_DIR.is_dir():
                tracked_paths = {str(sp.parents[1]) for sp in _status_paths.values()}
                try:
                    for encoded_dir in CLAUDE_PROJECTS_DIR.iterdir():
                        if not encoded_dir.is_dir():
                            continue
                        try:
                            jsonl_files = list(encoded_dir.glob("*.jsonl"))
                            if not jsonl_files:
                                continue
                            latest = max(jsonl_files, key=lambda p: p.stat().st_mtime)
                            if now_ts - latest.stat().st_mtime > JSONL_ACTIVE_SECONDS:
                                continue  # not active
                            parsed = _parse_jsonl_tail(latest)
                            cwd = parsed.get("cwd")
                            if not cwd or cwd in tracked_paths:
                                continue
                            # Check if this project sits under a known root
                            all_roots = [PROJECTS_ROOT] + _extra_roots
                            for root in all_roots:
                                if cwd.startswith(str(root)):
                                    _discover()  # re-run discovery to pick it up
                                    break
                        except OSError:
                            continue
                except OSError:
                    pass
        except Exception:
            pass
        await asyncio.sleep(2.0)


@app.on_event("startup")
async def startup() -> None:
    _load_roots_config()
    _discover()
    # Initial read of all status.json files
    for name, path in _status_paths.items():
        data = _read_status(path)
        if data is not None:
            # Load active agents on startup
            active_agents = []
            project_path = path.parents[1]
            agents_dir = project_path / ".claude" / "agents"
            if agents_dir.is_dir():
                now_ts = time.time()
                for agent_file in agents_dir.glob("*.json"):
                    try:
                        agent_data = json.loads(agent_file.read_text(encoding="utf-8"))
                        if agent_data.get("state") == "running":
                            active_agents.append(agent_data)
                        elif agent_data.get("state") == "done":
                            finished_at = agent_data.get("finished_at", "")
                            if finished_at:
                                try:
                                    from datetime import datetime
                                    ft = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
                                    age = now_ts - ft.timestamp()
                                    if age < 300:
                                        active_agents.append(agent_data)
                                except Exception:
                                    pass
                    except Exception:
                        pass
                active_agents.sort(key=lambda a: a.get("started_at", ""))
            data["active_agents"] = active_agents
            data["events"] = list(_project_events.get(name, []))
            hook_stats = data.get("stats") or {}
            jsonl_stats = _get_project_stats(path.parents[1], name)
            data["stats"] = {**jsonl_stats, **hook_stats}
            projects[name] = data
            _mtimes[str(path)] = path.stat().st_mtime
    asyncio.create_task(discovery_loop())
    asyncio.create_task(poll_loop())
    asyncio.create_task(jsonl_watcher_loop())


@app.get("/health")
async def health():
    return {"status": "ok", "projects_monitored": len(projects)}


@app.get("/api/version")
async def get_version():
    return {"version": VERSION, "build_date": BUILD_DATE}


@app.get("/api/diff")
async def get_diff(project: str = Query(...), file: str = Query(...)):
    """Returns the git diff for the specified file in the project."""
    project_path = PROJECTS_ROOT / project
    if not project_path.is_dir():
        return JSONResponse({"error": "project not found"}, status_code=404)

    file_path = Path(file)
    # Accepts absolute path or path relative to the project
    if not file_path.is_absolute():
        file_path = project_path / file_path

    if not file_path.is_file():
        return JSONResponse({"error": "file not found", "diff": ""})

    try:
        # 1. Try diff vs HEAD (tracked file with uncommitted changes)
        result = subprocess.run(
            ["git", "diff", "HEAD", "--", str(file_path)],
            cwd=str(project_path),
            capture_output=True, text=True, timeout=10,
        )
        diff = result.stdout.strip()

        # 2. If no diff vs HEAD, try staged only
        if not diff:
            result2 = subprocess.run(
                ["git", "diff", "--cached", "--", str(file_path)],
                cwd=str(project_path),
                capture_output=True, text=True, timeout=10,
            )
            diff = result2.stdout.strip()

        # 3. Only for UNTRACKED files (unknown to git) show the full file as new.
        #    Tracked files with no changes return an empty diff — should not fall through here.
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


@app.get("/api/status")
async def get_status():
    return {"projects": projects, "connected_clients": len(_sse_clients)}


@app.get("/events")
async def sse_events(request: Request):
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _sse_clients.append(queue)

    async def event_generator():
        # Send initial state on connect
        yield f"data: {json.dumps({'type': 'init', 'projects': projects, 'pending_projects': _pending_projects})}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            try:
                _sse_clients.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket):
    """Spawns the claude CLI in a PTY and bridges it to the browser via WebSocket."""
    await websocket.accept()

    claude_path = shutil.which("claude")
    if not claude_path:
        await websocket.send_bytes(b"\r\n\x1b[31mError: 'claude' not found in PATH\x1b[0m\r\n")
        await websocket.close()
        return

    master_fd, slave_fd = pty.openpty()

    def _set_winsize(fd: int, rows: int, cols: int) -> None:
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    _set_winsize(master_fd, 24, 120)

    env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"}

    proc = subprocess.Popen(
        [claude_path],
        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        env=env, close_fds=True, cwd=str(Path.home()),
    )
    os.close(slave_fd)

    loop = asyncio.get_event_loop()
    alive = True

    async def pty_to_ws() -> None:
        nonlocal alive
        try:
            while alive:
                data = await loop.run_in_executor(None, lambda: os.read(master_fd, 4096))
                if not data:
                    break
                await websocket.send_bytes(data)
        except (OSError, Exception):
            pass
        finally:
            alive = False

    async def ws_to_pty() -> None:
        nonlocal alive
        try:
            while alive:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                raw = msg.get("bytes") or (msg.get("text", "").encode() if msg.get("text") else None)
                if not raw:
                    continue
                # Control messages arrive as JSON text
                try:
                    obj = json.loads(raw)
                    if obj.get("type") == "resize":
                        _set_winsize(master_fd, int(obj["rows"]), int(obj["cols"]))
                    continue
                except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
                    pass
                try:
                    os.write(master_fd, raw)
                except OSError:
                    break
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            alive = False

    t1 = asyncio.create_task(pty_to_ws())
    t2 = asyncio.create_task(ws_to_pty())
    try:
        await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
    finally:
        alive = False
        t1.cancel()
        t2.cancel()
        try:
            proc.kill()
        except Exception:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass


@app.get("/api/pending")
async def get_pending_files(project: str = Query(...)):
    """Returns files with uncommitted git changes for the given project."""
    # Resolve project path from known roots
    project_path: Path | None = None
    if project in _status_paths:
        project_path = _status_paths[project].parents[1]
    else:
        for candidate in [PROJECTS_ROOT / project] + [r / project for r in _extra_roots]:
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
            # For renames: "old -> new" → take the new path
            if " -> " in rel:
                rel = rel.split(" -> ", 1)[1]
            rel = rel.strip().strip('"')
            # Combine XY: prefer index status (X), fall back to worktree (Y)
            code = xy[0].strip() or xy[1].strip() or "?"
            if code == "?":
                code = "?"  # untracked
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


@app.get("/api/weekly-stats")
async def get_weekly_stats():
    """Returns weekly token totals across all monitored projects."""
    result = {}
    for name, path in _status_paths.items():
        weekly_file = path.parent / "weekly_tokens.json"
        try:
            if weekly_file.exists():
                result[name] = json.loads(weekly_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"weekly": result}


def _parse_skill_md(content: str, name: str) -> dict:
    """Parse SKILL.md: extract YAML frontmatter + first body paragraph + heading."""
    lines = content.splitlines()
    frontmatter: dict[str, str] = {}
    body_start = 0

    # Parse YAML frontmatter between --- delimiters
    if lines and lines[0].strip() == "---":
        end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), None)
        if end:
            for l in lines[1:end]:
                if ":" in l:
                    k, _, v = l.partition(":")
                    frontmatter[k.strip()] = v.strip()
            body_start = end + 1

    title = frontmatter.get("name", name)
    description = frontmatter.get("description", "")
    argument_hint = frontmatter.get("argument-hint", "")

    # Extract first non-empty body paragraph (skip headings)
    body_lines: list[str] = []
    collecting = False
    for line in lines[body_start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            if collecting:
                break
            continue
        if stripped.startswith("---"):
            continue
        if stripped:
            collecting = True
            body_lines.append(stripped)
        elif collecting:
            break

    body_intro = " ".join(body_lines)[:300]

    # Fall back: use heading as title if no frontmatter name
    if title == name:
        for line in lines[body_start:]:
            if line.strip().startswith("# "):
                title = line.strip()[2:].strip()
                break

    return {
        "name": name,
        "title": title,
        "description": description,
        "argument_hint": argument_hint,
        "body_intro": body_intro,
    }


@app.get("/api/skills")
async def get_skills():
    """Returns list of available skills from ~/.claude/skills/ and standarts."""
    skills = []
    search_dirs = [
        (Path.home() / ".claude" / "skills", "user"),
        (PROJECTS_ROOT / "standarts" / "common" / "skills", "common"),
        (PROJECTS_ROOT / "standarts" / "private" / "skills", "private"),
        (PROJECTS_ROOT / "standarts" / "work" / "skills", "work"),
    ]
    for base, source in search_dirs:
        if not base.is_dir():
            continue
        for skill_md in base.glob("*/SKILL.md"):
            name = skill_md.parent.name
            try:
                content = skill_md.read_text(encoding="utf-8")
                parsed = _parse_skill_md(content, name)
                skills.append({**parsed, "source": source, "path": str(skill_md)})
            except Exception:
                skills.append({
                    "name": name, "title": name, "description": "",
                    "argument_hint": "", "body_intro": "",
                    "source": source, "path": str(skill_md),
                })
    skills.sort(key=lambda s: (s["source"], s["name"]))
    return {"skills": skills}


@app.get("/api/browse")
async def browse_directory(path: str = Query(default="")):
    """Lists subdirectories at path for the directory picker UI."""
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


@app.get("/api/config")
async def get_config():
    """Returns monitored roots configuration."""
    return {
        "primary_root": str(PROJECTS_ROOT),
        "extra_roots": [str(p) for p in _extra_roots],
    }


@app.post("/api/config/roots")
async def update_roots(request: Request):
    """Add or remove an extra monitored root directory."""
    global _extra_roots
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
        if p == PROJECTS_ROOT:
            return JSONResponse({"error": "This folder is already the primary folder"}, status_code=400)
        if p not in _extra_roots:
            _extra_roots.append(p)
            _save_roots_config()
            _discover()
    elif action == "remove":
        _extra_roots = [r for r in _extra_roots if r != p]
        _save_roots_config()
        _discover()
    else:
        return JSONResponse({"error": "action must be 'add' or 'remove'"}, status_code=400)

    return {
        "primary_root": str(PROJECTS_ROOT),
        "extra_roots": [str(r) for r in _extra_roots],
    }


@app.get("/api/claude-md")
async def get_claude_md(project: str = Query(...)):
    """Returns CLAUDE.md content for the given project."""
    project_path = PROJECTS_ROOT / project
    if not project_path.is_dir():
        # Try extra_roots
        for root in _extra_roots:
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


def _get_account_sync() -> dict:
    """Synchronous worker for account data — runs in a thread via asyncio.to_thread()
    to avoid blocking the event loop during the JSONL file scan (can be 1700+ files)."""
    from datetime import datetime, timedelta

    home = Path.home()

    # Settings
    settings: dict = {}
    try:
        sp = home / ".claude" / "settings.json"
        if sp.exists():
            settings = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        pass

    # Stats cache (daily activity — messages/sessions/tool calls)
    daily_activity: list = []
    try:
        sc = home / ".claude" / "stats-cache.json"
        if sc.exists():
            daily_activity = json.loads(sc.read_text(encoding="utf-8")).get("dailyActivity", [])
    except Exception:
        pass

    # Token aggregation from session JSONL files (last 7 days)
    week_ago = datetime.now() - timedelta(days=7)
    token_totals = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}
    service_tier: str = "standard"

    projects_dir = home / ".claude" / "projects"
    if projects_dir.is_dir():
        for jsonl_file in projects_dir.rglob("*.jsonl"):
            try:
                if datetime.fromtimestamp(jsonl_file.stat().st_mtime) < week_ago:
                    continue
                with jsonl_file.open(encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        if d.get("type") == "assistant" and "message" in d:
                            u = d["message"].get("usage", {})
                            token_totals["input"] += u.get("input_tokens", 0)
                            token_totals["output"] += u.get("output_tokens", 0)
                            token_totals["cache_creation"] += u.get("cache_creation_input_tokens", 0)
                            token_totals["cache_read"] += u.get("cache_read_input_tokens", 0)
                            if u.get("service_tier"):
                                service_tier = u["service_tier"]
            except Exception:
                pass

    return {
        "model": settings.get("model", "unknown"),
        "enabled_plugins": list((settings.get("enabledPlugins") or {}).keys()),
        "daily_activity": daily_activity,
        "tokens_week": token_totals,
        "service_tier": service_tier,
    }


@app.get("/api/account")
async def get_account():
    """Returns account settings and usage stats aggregated from session files."""
    return await asyncio.to_thread(_get_account_sync)


@app.delete("/api/file")
async def delete_file(project: str = Query(...), path: str = Query(...)):
    """Deletes an untracked file from a project. Only untracked (?) files are allowed."""
    # Resolve project root
    project_path: Path | None = None
    if project in _status_paths:
        project_path = _status_paths[project].parent.parent
    else:
        # Check pending projects
        for root in [PROJECTS_ROOT] + _extra_roots:
            candidate = root / project
            if candidate.is_dir() and (candidate / ".claude").is_dir():
                project_path = candidate
                break

    if project_path is None or not project_path.is_dir():
        return JSONResponse({"error": "project not found"}, status_code=404)

    # Resolve file path — accept absolute or relative
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = project_path / file_path
    file_path = file_path.resolve()

    # Safety: file must be inside the project directory
    try:
        file_path.relative_to(project_path.resolve())
    except ValueError:
        return JSONResponse({"error": "path outside project"}, status_code=400)

    if not file_path.exists():
        return JSONResponse({"error": "file not found"}, status_code=404)

    if not file_path.is_file():
        return JSONResponse({"error": "path is not a file"}, status_code=400)

    # Safety: only allow deleting untracked files (not tracked by git)
    try:
        ls = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(file_path)],
            cwd=str(project_path),
            capture_output=True, text=True, timeout=5,
        )
        if ls.returncode == 0:
            return JSONResponse({"error": "file is tracked by git — only untracked files can be deleted here"}, status_code=400)
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "timeout checking git status"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    try:
        file_path.unlink()
        return {"deleted": str(file_path)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# Serve static — must be last to avoid conflicting with the routes above
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")

import asyncio
import datetime
import difflib
import fcntl
import hashlib
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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

def _default_projects_root() -> str:
    """Returns the parent of the main git worktree, so worktrees resolve correctly.
    Falls back to the script's parent directory if git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list"],
            cwd=str(Path(__file__).parent),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            first_line = result.stdout.splitlines()[0]
            main_worktree = first_line.split()[0]
            return str(Path(main_worktree).parent)
    except Exception:
        pass
    return str(Path(__file__).parent.parent)

PROJECTS_ROOT = Path(os.getenv("PROJECTS_ROOT", _default_projects_root()))
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

# Agents dir mtime cache — tracks when agents dir changes so we rescan independently of status.json
_agents_dir_mtimes: dict[str, float] = {}  # project_name -> agents dir mtime

# Thinking block cache — last detected thinking block per project (for SSE deduplication)
_thinking_cache: dict[str, dict] = {}  # project_name -> {block_id, text, mtime}

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


def _detect_latest_thinking(jsonl_path: Path) -> dict | None:
    """Reads last 32 KB of JSONL, returns most recent non-empty thinking block.

    block_id = MD5 hash of entry timestamp — stable for a given assistant turn,
    changes when a new turn begins. Frontend uses block_id to distinguish
    replace (same id = update to current block) vs append (new id = new block).
    """
    try:
        with open(jsonl_path, "rb") as fh:
            fh.seek(0, 2); size = fh.tell()
            fh.seek(max(0, size - 32768))
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    last = None
    for line in raw.strip().split("\n"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "assistant":
            continue
        ts = d.get("timestamp", "")
        for c in (d.get("message", {}).get("content", []) or []):
            if isinstance(c, dict) and c.get("type") == "thinking":
                text = c.get("thinking", "").strip()
                if text:
                    block_id = hashlib.md5(ts.encode()).hexdigest()[:12]
                    last = {"block_id": block_id, "text": text,
                            "word_count": len(text.split()),
                            "timestamp": ts}
    return last


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


def _list_sessions(project_name: str) -> list[dict]:
    """Lists root-level JSONL sessions for a project, newest-first."""
    if project_name not in _status_paths:
        return []
    project_path = _status_paths[project_name].parents[1]
    encoded = str(project_path).replace("/", "-")
    jsonl_dir = CLAUDE_PROJECTS_DIR / encoded
    if not jsonl_dir.is_dir():
        return []
    now = time.time()
    sessions = []
    try:
        for f in jsonl_dir.glob("*.jsonl"):
            try:
                mtime = f.stat().st_mtime
                is_active = (now - mtime) <= JSONL_ACTIVE_SECONDS
                started_at = ended_at = None
                with f.open(encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                            ts = d.get("timestamp")
                            if ts and started_at is None:
                                started_at = ts
                            if ts:
                                ended_at = ts
                        except Exception:
                            continue
                sessions.append({
                    "session_id": f.stem,
                    "started_at": started_at,
                    "ended_at": None if is_active else ended_at,
                    "is_active": is_active,
                    "_mtime": mtime,
                })
            except OSError:
                continue
    except OSError:
        return []
    sessions.sort(key=lambda s: s["_mtime"], reverse=True)
    for s in sessions:
        del s["_mtime"]
    return sessions


def _tool_input_summary(name: str, inp: dict) -> str:
    if name in ("Read", "Write", "Edit"):
        return inp.get("file_path", inp.get("path", ""))
    if name == "Bash":
        return inp.get("command", "")[:80]
    if name in ("Glob", "Grep"):
        return inp.get("pattern", inp.get("path", ""))
    if name in ("WebFetch", "WebSearch"):
        return inp.get("url", inp.get("query", ""))
    for v in inp.values():
        if isinstance(v, str):
            return v[:80]
    return ""


def _tool_detail(name: str, inp: dict) -> dict:
    """Returns structured detail for a tool call, used in the insights modal."""
    if name == "Bash":
        return {
            "type": "bash",
            "command": inp.get("command", ""),
            "description": inp.get("description", ""),
        }
    if name == "Edit":
        file_path = inp.get("file_path", inp.get("path", ""))
        old = inp.get("old_string", "")
        new = inp.get("new_string", "")
        diff_lines = list(difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm="",
        ))
        return {
            "type": "edit",
            "file_path": file_path,
            "diff": "".join(diff_lines),
        }
    if name == "Write":
        content = inp.get("content", "")
        return {
            "type": "write",
            "file_path": inp.get("file_path", inp.get("path", "")),
            "content": content[:3000],
            "total_chars": len(content),
        }
    if name == "Read":
        return {
            "type": "read",
            "file_path": inp.get("file_path", inp.get("path", "")),
            "limit": inp.get("limit"),
            "offset": inp.get("offset"),
        }
    if name in ("Grep", "Glob"):
        return {
            "type": "search",
            "tool": name,
            "pattern": inp.get("pattern", ""),
            "path": inp.get("path", ""),
            "include": inp.get("include", ""),
        }
    if name in ("WebFetch", "WebSearch"):
        return {
            "type": "web",
            "url": inp.get("url", ""),
            "query": inp.get("query", ""),
        }
    if name == "Agent":
        return {
            "type": "agent",
            "description": inp.get("description", ""),
            "prompt": inp.get("prompt", inp.get("instructions", ""))[:500],
        }
    # Generic
    return {
        "type": "generic",
        "fields": {k: str(v)[:500] for k, v in inp.items() if isinstance(v, str)},
    }


def _parse_session_detail(jsonl_path: Path) -> dict:
    thinking = []
    tools = []
    stats = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "model": ""}
    entries = []
    try:
        with jsonl_path.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        return {"thinking": [], "tools": [], "stats": stats}

    # Index tool_results by id for duration + success
    tool_results: dict[str, dict] = {}
    for entry in entries:
        if entry.get("type") != "user":
            continue
        for c in (entry.get("message", {}).get("content", []) or []):
            if isinstance(c, dict) and c.get("type") == "tool_result":
                tool_results[c.get("tool_use_id", "")] = {
                    "timestamp": entry.get("timestamp"),
                    "is_error": c.get("is_error", False),
                }

    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", [])
        ts = entry.get("timestamp", "")
        if not isinstance(content, list):
            continue
        u = msg.get("usage", {})
        stats["input_tokens"]      += u.get("input_tokens", 0)
        stats["output_tokens"]     += u.get("output_tokens", 0)
        stats["cache_read_tokens"] += u.get("cache_read_input_tokens", 0)
        m = msg.get("model", "")
        if m:
            stats["model"] = m
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "thinking":
                text = c.get("thinking", "").strip()
                if text:
                    thinking.append({"text": text, "timestamp": ts,
                                     "word_count": len(text.split())})
            if c.get("type") == "tool_use":
                tid = c.get("id", "")
                tname = c.get("name", "")
                tinput = c.get("input", {})
                result = tool_results.get(tid, {})
                duration_ms = None
                rts = result.get("timestamp")
                if ts and rts:
                    try:
                        t1 = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        t2 = datetime.datetime.fromisoformat(rts.replace("Z", "+00:00"))
                        duration_ms = int((t2 - t1).total_seconds() * 1000)
                    except Exception:
                        pass
                tools.append({
                    "tool": tname,
                    "input": _tool_input_summary(tname, tinput),
                    "detail": _tool_detail(tname, tinput),
                    "duration_ms": duration_ms,
                    "success": not result.get("is_error", False),
                    "timestamp": ts,
                })
    return {"thinking": thinking, "tools": tools[-50:], "stats": stats}


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
                        # Don't override "compacting" — PreCompact hook set it.
                        # Don't override "waiting" — Notification hook set it; user hasn't responded.
                        # Only flip to working when JSONL is substantially newer (>2s).
                        cur_data_state = data.get("state") or data.get("status", "idle")
                        notification_active = bool(data.get("notification")) and cur_data_state in ("waiting", "notification")
                        if cur_data_state == "compacting":
                            pass  # preserve compacting
                        elif notification_active:
                            # Notification hook fired before JSONL write completed (common).
                            # JSONL newer than status.json does NOT mean Claude is working —
                            # it means the hook fired first. Preserve "waiting" until a new
                            # hook (PreToolUse after user responds) clears the notification.
                            pass
                        else:
                            data["state"] = "working"
                            data["status"] = "working"
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
                                if agent_data.get("state") == "running":
                                    # Guard against stale "running" agents: if last_updated
                                    # (or started_at) is older than 10 minutes, the parent
                                    # session died without marking the agent done.
                                    ts_str = agent_data.get("last_updated") or agent_data.get("started_at", "")
                                    stale = False
                                    if ts_str:
                                        try:
                                            ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                            stale = (agents_now - ts.timestamp()) > 600  # 10 min
                                        except Exception:
                                            pass
                                    if not stale:
                                        active_agents.append(agent_data)
                                elif agent_data.get("state") == "done":
                                    finished_at = agent_data.get("finished_at", "")
                                    if finished_at:
                                        try:
                                            ft = datetime.datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
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
            else:
                # status.json didn't change — but check if agents dir changed independently.
                # This catches agent files added/deleted without a status.json write.
                project_path = path.parents[1]
                agents_dir = project_path / ".claude" / "agents"
                if agents_dir.is_dir():
                    try:
                        agents_dir_mtime = agents_dir.stat().st_mtime
                    except OSError:
                        agents_dir_mtime = 0.0
                    if _agents_dir_mtimes.get(name) != agents_dir_mtime:
                        _agents_dir_mtimes[name] = agents_dir_mtime
                        # Re-scan agents and broadcast if list changed
                        current = projects.get(name)
                        if current is not None:
                            agents_now = time.time()
                            active_agents = []
                            for agent_file in agents_dir.glob("*.json"):
                                try:
                                    agent_data = json.loads(agent_file.read_text(encoding="utf-8"))
                                    if agent_data.get("state") == "running":
                                        ts_str = agent_data.get("last_updated") or agent_data.get("started_at", "")
                                        stale = False
                                        if ts_str:
                                            try:
                                                ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                                stale = (agents_now - ts.timestamp()) > 600
                                            except Exception:
                                                pass
                                        if not stale:
                                            active_agents.append(agent_data)
                                    elif agent_data.get("state") == "done":
                                        finished_at = agent_data.get("finished_at", "")
                                        if finished_at:
                                            try:
                                                ft = datetime.datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
                                                if (agents_now - ft.timestamp()) < 300:
                                                    active_agents.append(agent_data)
                                            except Exception:
                                                pass
                                except Exception:
                                    pass
                            active_agents.sort(key=lambda a: a.get("started_at", ""))
                            prev_ids = {a.get("agent_id") for a in current.get("active_agents", [])}
                            new_ids = {a.get("agent_id") for a in active_agents}
                            if prev_ids != new_ids:
                                updated = dict(current)
                                updated["active_agents"] = active_agents
                                projects[name] = updated
                                _broadcast({"type": "update", "project_name": name, "data": updated, "pending_projects": _pending_projects})

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
                    # No JSONL at all — if stuck in working state, flip to idle
                    current = projects.get(name, {})
                    cur_state = current.get("state") or current.get("status", "idle")
                    if cur_state == "working":
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

                    thinking = _detect_latest_thinking(latest_jsonl)
                    if thinking:
                        prev = _thinking_cache.get(name, {})
                        if (prev.get("block_id") != thinking["block_id"] or
                                prev.get("text") != thinking["text"]):
                            _thinking_cache[name] = {
                                "block_id": thinking["block_id"],
                                "text": thinking["text"],
                                "mtime": latest_mtime,
                            }
                            _broadcast({
                                "type": "thinking",
                                "project": name,
                                "block_id": thinking["block_id"],
                                "text": thinking["text"],
                                "word_count": thinking["word_count"],
                                "timestamp": thinking["timestamp"],
                            })

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
                        # Don't override "compacting" — PreCompact hook set it and we must
                        # preserve it until the new session's first hook fires.
                        # Don't override "waiting" — Notification hook set it and the user
                        # hasn't responded yet. Only clear when JSONL is substantially newer
                        # (>2s), meaning Claude resumed after the user responded.
                        notification_active = bool(updated.get("notification")) and cur_state in ("waiting", "notification")
                        if cur_state == "compacting":
                            pass  # preserve compacting
                        elif notification_active:
                            # Notification hook fired before JSONL write completed (common).
                            # JSONL newer than status.json does NOT mean Claude resumed —
                            # preserve "waiting" until a new PreToolUse hook clears it.
                            pass
                        else:
                            updated["state"] = "working"
                            updated["status"] = "working"
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
                            merged = {**hook_stats, **jsonl_stats}
                            # Guard: if JSONL has no assistant messages yet (new session file),
                            # _get_project_stats returns ctx=0 and model="". Don't let that
                            # overwrite valid hook values — it would zero out the ctx% display.
                            if not jsonl_stats.get("session_ctx_tokens") and hook_stats.get("session_ctx_tokens"):
                                merged["session_ctx_tokens"] = hook_stats["session_ctx_tokens"]
                            if not jsonl_stats.get("model") and hook_stats.get("model"):
                                merged["model"] = hook_stats["model"]
                            updated["stats"] = merged
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
                            merged = {**hook_stats, **jsonl_stats}
                            if not jsonl_stats.get("session_ctx_tokens") and hook_stats.get("session_ctx_tokens"):
                                merged["session_ctx_tokens"] = hook_stats["session_ctx_tokens"]
                            if not jsonl_stats.get("model") and hook_stats.get("model"):
                                merged["model"] = hook_stats["model"]
                            updated["stats"] = merged
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
                            ts_str = agent_data.get("last_updated") or agent_data.get("started_at", "")
                            stale = False
                            if ts_str:
                                try:
                                    ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                    stale = (now_ts - ts.timestamp()) > 600
                                except Exception:
                                    pass
                            if not stale:
                                active_agents.append(agent_data)
                        elif agent_data.get("state") == "done":
                            finished_at = agent_data.get("finished_at", "")
                            if finished_at:
                                try:
                                    ft = datetime.datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
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


@app.get("/")
async def root():
    return RedirectResponse(url="/insights")

@app.get("/insights")
async def insights_page():
    return FileResponse("static/insights.html")


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


@app.get("/api/sessions")
async def get_sessions(project: str = Query(...)):
    """Lists JSONL sessions for a project, newest-first."""
    if project not in _status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    return _list_sessions(project)


@app.get("/api/session-detail")
async def get_session_detail(project: str = Query(...), session_id: str = Query(...)):
    """Returns thinking blocks, tool events and stats for a session."""
    if project not in _status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    project_path = _status_paths[project].parents[1]
    encoded = str(project_path).replace("/", "-")
    jsonl_path = CLAUDE_PROJECTS_DIR / encoded / f"{session_id}.jsonl"
    if not jsonl_path.is_file():
        return JSONResponse({"error": "session not found"}, status_code=404)
    return _parse_session_detail(jsonl_path)


@app.get("/api/insights-stats")
async def get_insights_stats(project: str = Query(...)):
    """Aggregated metrics for the last 7 days."""
    if project not in _status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    project_path = _status_paths[project].parents[1]
    encoded = str(project_path).replace("/", "-")
    jsonl_dir = CLAUDE_PROJECTS_DIR / encoded
    if not jsonl_dir.is_dir():
        return {"sessions_count": 0, "sessions_7d": 0, "total_tokens": 0,
                "cache_hit_pct": 0, "top_tool": None, "top_tool_count": 0}
    cutoff = time.time() - 7 * 24 * 3600
    sessions_total = 0   # all-time session count
    sessions_count = 0   # last-7-days session count (for token aggregation)
    total_input = total_output = total_cache = 0
    tool_counts: dict[str, int] = {}
    try:
        for f in jsonl_dir.glob("*.jsonl"):
            try:
                mtime = f.stat().st_mtime
                sessions_total += 1
                if mtime < cutoff:
                    continue
                sessions_count += 1
                with f.open(encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        if d.get("type") == "assistant":
                            u = d.get("message", {}).get("usage", {})
                            total_input  += u.get("input_tokens", 0)
                            total_output += u.get("output_tokens", 0)
                            total_cache  += u.get("cache_read_input_tokens", 0)
                            for c in d.get("message", {}).get("content", []):
                                if isinstance(c, dict) and c.get("type") == "tool_use":
                                    n = c.get("name", "")
                                    if n:
                                        tool_counts[n] = tool_counts.get(n, 0) + 1
            except OSError:
                continue
    except OSError:
        pass
    total_tokens = total_input + total_output
    total_real   = total_input + total_cache
    cache_hit_pct = round(total_cache / total_real * 100) if total_real > 0 else 0
    top_tool = max(tool_counts, key=tool_counts.get) if tool_counts else None
    return {
        "sessions_count": sessions_total,
        "sessions_7d": sessions_count,
        "total_tokens": total_tokens,
        "cache_hit_pct": cache_hit_pct,
        "top_tool": top_tool,
        "top_tool_count": tool_counts.get(top_tool, 0) if top_tool else 0,
    }


@app.get("/api/usage-window")
async def get_usage_window(project: str = Query(...)):
    """Tokens consumed in the current 5-hour rolling window."""
    WINDOW_SECS = 5 * 3600
    if project not in _status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    project_path = _status_paths[project].parents[1]
    encoded = str(project_path).replace("/", "-")
    jsonl_dir = CLAUDE_PROJECTS_DIR / encoded
    if not jsonl_dir.is_dir():
        return {"window_tokens": 0, "window_start": None, "window_end": None, "sessions_in_window": 0}

    now = time.time()
    cutoff = now - WINDOW_SECS
    window_tokens = 0
    window_start_ts: float | None = None
    sessions_in_window = 0

    try:
        for f in jsonl_dir.glob("*.jsonl"):
            try:
                mtime = f.stat().st_mtime
                if mtime < cutoff:
                    continue
                sessions_in_window += 1
                if window_start_ts is None or mtime < window_start_ts:
                    window_start_ts = mtime
                with f.open(encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        if d.get("type") == "assistant":
                            u = d.get("message", {}).get("usage", {})
                            window_tokens += u.get("input_tokens", 0) + u.get("output_tokens", 0)
            except OSError:
                continue
    except OSError:
        pass

    # Window anchored to when the oldest session in window started
    window_start = window_start_ts or now
    window_end = window_start + WINDOW_SECS
    elapsed_secs = now - window_start
    remaining_secs = max(0, window_end - now)

    return {
        "window_tokens": window_tokens,
        "window_start": window_start,
        "window_end": window_end,
        "elapsed_secs": int(elapsed_secs),
        "remaining_secs": int(remaining_secs),
        "elapsed_pct": min(100, round((elapsed_secs / WINDOW_SECS) * 100)),
        "sessions_in_window": sessions_in_window,
    }


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


@app.get("/api/context-inspect")
async def get_context_inspect(project: str = Query(...), session_id: str = Query(default="")):
    """Returns context window breakdown: rules loaded + files read this session."""
    if project not in _status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    project_path = _status_paths[project].parents[1]

    # ── 1. Rules / system context ──────────────────────────────────────────
    rules: list[dict] = []

    def _add_rule(label: str, real_path: Path, category: str) -> None:
        try:
            size = real_path.stat().st_size
            rules.append({
                "label": label,
                "real_path": str(real_path),
                "size_bytes": size,
                "tokens_est": size // 4,
                "category": category,
            })
        except OSError:
            pass

    # Project CLAUDE.md (root + .claude/)
    for candidate in [project_path / "CLAUDE.md", project_path / ".claude" / "CLAUDE.md"]:
        if candidate.is_file():
            _add_rule(candidate.name, candidate, "claude-md")

    # Global CLAUDE.md
    global_claude = Path.home() / ".claude" / "CLAUDE.md"
    if global_claude.is_file():
        _add_rule("~/.claude/CLAUDE.md", global_claude, "global")

    # .claude/rules/ — handles both file symlinks and directory symlinks
    # Common pattern: common -> standarts/common/rules/, private -> standarts/private/rules/
    rules_dir = project_path / ".claude" / "rules"
    if rules_dir.is_dir():
        for entry in sorted(rules_dir.iterdir()):
            try:
                real = entry.resolve()
                if real.is_file():
                    # Direct file or file symlink
                    label = entry.name
                    try:
                        label = str(real.relative_to(PROJECTS_ROOT))
                    except ValueError:
                        pass
                    _add_rule(label, real, "rule")
                elif real.is_dir():
                    # Symlink pointing to a directory — scan all .md files within
                    for md_file in sorted(real.rglob("*.md")):
                        if md_file.is_file():
                            label = md_file.name
                            try:
                                label = str(md_file.relative_to(PROJECTS_ROOT))
                            except ValueError:
                                pass
                            _add_rule(label, md_file, "rule")
            except OSError:
                pass

    # Global rules ~/.claude/rules/ — same logic
    global_rules = Path.home() / ".claude" / "rules"
    if global_rules.is_dir():
        for entry in sorted(global_rules.iterdir()):
            try:
                real = entry.resolve()
                if real.is_file():
                    _add_rule(f"~/.claude/rules/{entry.name}", real, "global-rule")
                elif real.is_dir():
                    for md_file in sorted(real.rglob("*.md")):
                        if md_file.is_file():
                            _add_rule(f"~/.claude/rules/{entry.name}/{md_file.name}", md_file, "global-rule")
            except OSError:
                pass

    rules.sort(key=lambda r: r["size_bytes"], reverse=True)
    rules_total_bytes = sum(r["size_bytes"] for r in rules)

    # ── 2. Files read in session (from JSONL) ─────────────────────────────
    reads: list[dict] = []

    encoded = str(project_path).replace("/", "-")
    jsonl_dir = CLAUDE_PROJECTS_DIR / encoded

    # Pick session_id or latest
    jsonl_path: Path | None = None
    if session_id:
        candidate = jsonl_dir / f"{session_id}.jsonl"
        if candidate.is_file():
            jsonl_path = candidate
    if jsonl_path is None:
        latest, _ = _get_latest_jsonl(project_path)
        jsonl_path = latest

    if jsonl_path and jsonl_path.is_file():
        # Parse: index tool_use by id, then match tool_result
        tool_uses: dict[str, dict] = {}  # id -> {name, input}
        try:
            with jsonl_path.open(encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("type") == "assistant":
                        for c in (d.get("message", {}).get("content", []) or []):
                            if isinstance(c, dict) and c.get("type") == "tool_use":
                                tool_uses[c["id"]] = {
                                    "name": c.get("name", ""),
                                    "input": c.get("input", {}),
                                }
                    elif d.get("type") == "user":
                        for c in (d.get("message", {}).get("content", []) or []):
                            if not isinstance(c, dict) or c.get("type") != "tool_result":
                                continue
                            tid = c.get("tool_use_id", "")
                            tu = tool_uses.get(tid, {})
                            tool_name = tu.get("name", "")
                            if tool_name not in ("Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch"):
                                continue
                            # Compute result size
                            result_content = c.get("content", "")
                            if isinstance(result_content, list):
                                result_text = "\n".join(
                                    x.get("text", "") for x in result_content
                                    if isinstance(x, dict)
                                )
                            else:
                                result_text = str(result_content)
                            size_bytes = len(result_text.encode("utf-8"))
                            inp = tu.get("input", {})
                            label = (
                                inp.get("file_path") or inp.get("path") or
                                inp.get("command", "")[:60] or
                                inp.get("url") or inp.get("query") or
                                inp.get("pattern") or ""
                            )
                            reads.append({
                                "tool": tool_name,
                                "label": label,
                                "size_bytes": size_bytes,
                                "tokens_est": size_bytes // 4,
                                "is_error": c.get("is_error", False),
                                "content": result_text[:8000],
                                "total_chars": len(result_text),
                            })
        except OSError:
            pass

    # Deduplicate — keep most recently accessed per (tool, label), sort by recency
    last_pos: dict[tuple, int] = {}
    items: dict[tuple, dict] = {}
    for i, r in enumerate(reads):
        key = (r["tool"], r["label"])
        last_pos[key] = i
        items[key] = r  # always overwrite with most recent occurrence
    reads = [items[k] for k in sorted(last_pos, key=lambda k: last_pos[k], reverse=True)]
    reads_total_bytes = sum(r["size_bytes"] for r in reads)

    # Conversation messages — user text + assistant text (excluding tool calls/results)
    messages: list[dict] = []
    conv_total_bytes = 0
    if jsonl_path and jsonl_path.is_file():
        try:
            with jsonl_path.open(encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    role = d.get("type")  # "user" or "assistant"
                    if role not in ("user", "assistant"):
                        continue
                    content = d.get("message", {}).get("content", []) or []
                    text_parts: list[str] = []
                    if isinstance(content, str):
                        text_parts = [content]
                    else:
                        for c in content:
                            if not isinstance(c, dict):
                                continue
                            # Skip tool_use and tool_result blocks
                            if c.get("type") in ("tool_use", "tool_result"):
                                continue
                            if c.get("type") == "text":
                                text_parts.append(c.get("text", ""))
                            elif c.get("type") == "thinking":
                                pass  # skip thinking blocks
                    text = "\n".join(text_parts).strip()
                    if not text:
                        continue
                    # Skip system metadata injected as user messages (commands, hooks, etc.)
                    if text.startswith("<command-") or text.startswith("<local-command") or text.startswith("<system-reminder"):
                        continue
                    size_bytes = len(text.encode("utf-8"))
                    conv_total_bytes += size_bytes
                    is_compaction = role == "user" and text.startswith("This session is being continued from a previous conversation")
                    messages.append({
                        "role": role,
                        "is_compaction": is_compaction,
                        "snippet": text[:120],
                        "full_text": text[:8000],
                        "total_chars": len(text),
                        "size_bytes": size_bytes,
                        "tokens_est": size_bytes // 4,
                    })
        except OSError:
            pass

    return {
        "rules": rules,
        "rules_total_bytes": rules_total_bytes,
        "rules_total_tokens_est": rules_total_bytes // 4,
        "reads": reads[:60],  # top 60
        "reads_total_bytes": reads_total_bytes,
        "reads_total_tokens_est": reads_total_bytes // 4,
        "messages": list(reversed(messages[-50:])),  # most recent first, last 50
        "conv_total_bytes": conv_total_bytes,
        "conv_total_tokens_est": conv_total_bytes // 4,
        "session_id": str(jsonl_path.stem) if jsonl_path else None,
    }


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


@app.get("/api/file-preview")
async def get_file_preview(path: str = Query(...)):
    """Return content of a .md file for the context inspector modal."""
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


# Serve static — must be last to avoid conflicting with the routes above
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")

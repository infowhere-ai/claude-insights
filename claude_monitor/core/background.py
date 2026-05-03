"""Background asyncio loops — discovery, status polling, JSONL watcher."""

import asyncio
import datetime
import time
from pathlib import Path

from claude_monitor import config, state
from claude_monitor.core import broadcast as _broadcast_mod
from claude_monitor.jsonl import parser
from claude_monitor.projects import service as project_service
from claude_monitor.sessions import service as session_service
from claude_monitor.stats import service as stats_service


# ---------------------------------------------------------------------------
# Private helpers — extracted from poll_loop
# ---------------------------------------------------------------------------


def _should_override_with_jsonl(data: dict, jsonl_info: dict, now_ts: float, mtime: float) -> bool:
    """Return True if JSONL is newer and active, so it should override status."""
    jsonl_mtime = jsonl_info.get("mtime", 0.0)
    if not jsonl_mtime:
        return False
    if jsonl_mtime <= mtime:
        return False
    if (now_ts - jsonl_mtime) > config.JSONL_ACTIVE_SECONDS:
        return False
    return True


def _apply_jsonl_state(data: dict, jsonl_info: dict) -> None:
    """Apply tool/state from JSONL cache onto the data dict (mutates in place)."""
    cur_data_state = data.get("state") or data.get("status", "idle")
    notification_active = bool(data.get("notification")) and cur_data_state in (
        "waiting",
        "notification",
    )
    if cur_data_state == "compacting":
        pass
    elif notification_active:
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


def _build_event(data: dict) -> dict:
    """Build the SSE event dict from project data."""
    return {
        "timestamp": data.get(
            "ts",
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ),
        "status": data.get("status", "idle"),
        "tool": data.get("tool"),
        "message": data.get("tool")
        if data.get("status") == "working"
        else data.get("status", "idle"),
        "hook": "PreToolUse" if data.get("status") == "working" else "PostToolUse",
    }


def _handle_agent_changes(name: str, path: Path, project_path: Path, now_ts: float) -> None:
    """Handle agents dir mtime change — persist done agents and broadcast if changed."""
    current = state.projects.get(name)
    if current is None:
        return
    agents_dir = project_path / ".claude" / "agents"
    active_agents = session_service.persist_done_agents(
        agents_dir,
        name,
        session_service.current_session_id(name),
        now_ts,
    )
    prev_ids = {a.get("id") for a in current.get("active_agents", [])}
    new_ids = {a.get("id") for a in active_agents}
    if prev_ids != new_ids:
        updated = dict(current)
        updated["active_agents"] = active_agents
        state.projects[name] = updated
        _broadcast_mod.broadcast(
            {
                "type": "update",
                "project_name": name,
                "data": updated,
                "pending_projects": state._pending_projects,
            }
        )


# ---------------------------------------------------------------------------
# Private helpers — extracted from jsonl_watcher_loop
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return current UTC datetime as ISO 8601 string with +00:00."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _make_idle_update(current: dict) -> dict:
    """Return a copy of current with state/status set to idle."""
    stale = dict(current)
    stale["status"] = "idle"
    stale["state"] = "idle"
    now_iso = _now_iso()
    stale["ts"] = now_iso
    stale["updated_at"] = now_iso
    stale["message"] = "idle"
    stale["_stale"] = True
    return stale


def _merge_stats(
    updated: dict,
    project_path: Path,
    name: str,
    latest_jsonl: Path,
    latest_mtime: float,
) -> None:
    """Merge JSONL stats into updated dict if stale (mutates in place)."""
    stats_stale = state._jsonl_mtimes.get(str(latest_jsonl)) != latest_mtime
    if not stats_stale:
        return
    hook_stats = updated.get("stats") or {}
    jsonl_stats = stats_service.get_project_stats(project_path, name)
    merged = {**hook_stats, **jsonl_stats}
    if not jsonl_stats.get("session_ctx_tokens") and hook_stats.get("session_ctx_tokens"):
        merged["session_ctx_tokens"] = hook_stats["session_ctx_tokens"]
    if not jsonl_stats.get("model") and hook_stats.get("model"):
        merged["model"] = hook_stats["model"]
    updated["stats"] = merged


def _process_active_project(
    name: str,
    sp: Path,
    cached: dict,
    current: dict,
    project_path: Path,
    now_ts: float,
    latest_jsonl: Path,
    latest_mtime: float,
) -> None:
    """Process a project with an active JSONL — update state and broadcast."""
    cur_state = current.get("state") or current.get("status", "idle")
    tool = cached.get("tool") or ""
    cur_action = current.get("current_action")
    cur_tool = cur_action.get("tool") if isinstance(cur_action, dict) else cur_action
    stats_stale = state._jsonl_mtimes.get(str(latest_jsonl)) != latest_mtime

    if cur_state == "working" and cur_tool == tool and not stats_stale:
        return

    updated = dict(current)
    notification_active = bool(updated.get("notification")) and cur_state in (
        "waiting",
        "notification",
    )
    if cur_state == "compacting":
        pass
    elif notification_active:
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
    updated["ts"] = _now_iso()
    if stats_stale:
        _merge_stats(updated, project_path, name, latest_jsonl, latest_mtime)
        state._jsonl_mtimes[str(latest_jsonl)] = latest_mtime
    state.projects[name] = updated
    _broadcast_mod.broadcast(
        {
            "type": "update",
            "project_name": name,
            "data": updated,
            "pending_projects": state._pending_projects,
        }
    )


# ---------------------------------------------------------------------------
# Background loops
# ---------------------------------------------------------------------------


async def discovery_loop() -> None:  # pragma: no cover
    while True:
        project_service.discover()
        await asyncio.sleep(config.DISCOVERY_INTERVAL)


async def poll_loop() -> None:  # pragma: no cover
    await asyncio.sleep(2)
    while True:
        now_ts = time.time()
        for name, path in list(state._status_paths.items()):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            path_str = str(path)
            if state._mtimes.get(path_str) != mtime:
                state._mtimes[path_str] = mtime
                data = project_service.read_status(path)
                if data is not None:
                    jsonl_info = state._jsonl_cache.get(name, {})
                    if _should_override_with_jsonl(data, jsonl_info, now_ts, mtime):
                        _apply_jsonl_state(data, jsonl_info)

                    project_path = path.parents[1]
                    agents_dir = project_path / ".claude" / "agents"
                    new_state = data.get("state") or data.get("status", "idle")
                    prev_state = (state.projects.get(name) or {}).get("state") or (
                        state.projects.get(name) or {}
                    ).get("status", "idle")
                    if new_state == "stopped" and prev_state != "stopped":
                        session_service.persist_and_clean_session(
                            name, data, agents_dir if agents_dir.is_dir() else None
                        )
                        active_agents = []
                    elif agents_dir.is_dir():
                        active_agents = session_service.persist_done_agents(
                            agents_dir, name, session_service.current_session_id(name), now_ts
                        )
                    else:
                        active_agents = []
                    data["active_agents"] = active_agents

                    if any(a.get("state") == "running" for a in active_agents):
                        data["state"] = "working"
                        data["status"] = "working"
                        data["notification"] = None

                    event = _build_event(data)
                    events = state._project_events.setdefault(name, [])
                    events.append(event)
                    if len(events) > 500:
                        events[:] = events[-500:]
                    data["events"] = list(events)

                    project_path = state._status_paths[name].parents[1]
                    hook_stats = data.get("stats") or {}
                    jsonl_stats = stats_service.get_project_stats(project_path, name)
                    data["stats"] = {**jsonl_stats, **hook_stats}

                    state.projects[name] = data
                    _broadcast_mod.broadcast(
                        {
                            "type": "update",
                            "project_name": name,
                            "data": data,
                            "pending_projects": state._pending_projects,
                        }
                    )
            else:
                project_path = path.parents[1]
                agents_dir = project_path / ".claude" / "agents"
                if agents_dir.is_dir():
                    try:
                        agents_dir_mtime = agents_dir.stat().st_mtime
                    except OSError:
                        agents_dir_mtime = 0.0
                    if state._agents_dir_mtimes.get(name) != agents_dir_mtime:
                        state._agents_dir_mtimes[name] = agents_dir_mtime
                        _handle_agent_changes(name, path, project_path, time.time())

        await asyncio.sleep(config.POLL_INTERVAL)


async def jsonl_watcher_loop() -> None:  # pragma: no cover
    await asyncio.sleep(3)
    while True:
        try:
            now_ts = time.time()

            for name, sp in list(state._status_paths.items()):
                project_path = sp.parents[1]
                latest_jsonl, latest_mtime = parser.get_latest_jsonl(project_path)
                if latest_jsonl is None:
                    current = state.projects.get(name, {})
                    cur_state = current.get("state") or current.get("status", "idle")
                    if cur_state == "working":
                        stale = _make_idle_update(current)
                        state.projects[name] = stale
                        _broadcast_mod.broadcast(
                            {
                                "type": "update",
                                "project_name": name,
                                "data": stale,
                                "pending_projects": state._pending_projects,
                            }
                        )
                    continue

                cached = state._jsonl_cache.get(name, {})
                if cached.get("mtime") != latest_mtime:
                    parsed = parser.parse_jsonl_tail(latest_jsonl)
                    state._jsonl_cache[name] = {
                        "mtime": latest_mtime,
                        "tool": parsed.get("tool"),
                        "jsonl_path": str(latest_jsonl),
                    }
                    cached = state._jsonl_cache[name]

                    thinking = parser.detect_latest_thinking(latest_jsonl)
                    if thinking:
                        prev = state._thinking_cache.get(name, {})
                        if (
                            prev.get("block_id") != thinking["block_id"]
                            or prev.get("text") != thinking["text"]
                        ):
                            state._thinking_cache[name] = {
                                "block_id": thinking["block_id"],
                                "text": thinking["text"],
                                "mtime": latest_mtime,
                            }
                            _broadcast_mod.broadcast(
                                {
                                    "type": "thinking",
                                    "project": name,
                                    "block_id": thinking["block_id"],
                                    "text": thinking["text"],
                                    "word_count": thinking["word_count"],
                                    "timestamp": thinking["timestamp"],
                                }
                            )

                age = now_ts - latest_mtime
                current = state.projects.get(name, {})
                cur_state = current.get("state") or current.get("status", "idle")

                status_mtime = 0.0
                try:
                    status_mtime = sp.stat().st_mtime
                except OSError:
                    pass

                if age <= config.JSONL_ACTIVE_SECONDS and latest_mtime > status_mtime:
                    _process_active_project(
                        name, sp, cached, current, project_path, now_ts, latest_jsonl, latest_mtime
                    )
                else:
                    has_running_agents = any(
                        a.get("state") == "running" for a in current.get("active_agents", [])
                    )
                    if cur_state == "working" and not has_running_agents:
                        stale = _make_idle_update(current)
                        state.projects[name] = stale
                        _broadcast_mod.broadcast(
                            {
                                "type": "update",
                                "project_name": name,
                                "data": stale,
                                "pending_projects": state._pending_projects,
                            }
                        )
                    elif cur_state == "working" and has_running_agents:
                        updated = dict(current)
                        _merge_stats(updated, project_path, name, latest_jsonl, latest_mtime)
                        updated["ts"] = _now_iso()
                        state.projects[name] = updated
                        _broadcast_mod.broadcast(
                            {
                                "type": "update",
                                "project_name": name,
                                "data": updated,
                                "pending_projects": state._pending_projects,
                            }
                        )

            if config.CLAUDE_PROJECTS_DIR.is_dir():
                tracked_paths = {str(sp.parents[1]) for sp in state._status_paths.values()}
                try:
                    for encoded_dir in config.CLAUDE_PROJECTS_DIR.iterdir():
                        if not encoded_dir.is_dir():
                            continue
                        try:
                            jsonl_files = list(encoded_dir.glob("*.jsonl"))
                            if not jsonl_files:
                                continue
                            latest = max(jsonl_files, key=lambda p: p.stat().st_mtime)
                            if now_ts - latest.stat().st_mtime > config.JSONL_ACTIVE_SECONDS:
                                continue
                            parsed = parser.parse_jsonl_tail(latest)
                            cwd = parsed.get("cwd")
                            if not cwd or cwd in tracked_paths:
                                continue
                            all_roots = [config.PROJECTS_ROOT] + list(state._extra_roots)
                            for root in all_roots:
                                if cwd.startswith(str(root)):
                                    project_service.discover()
                                    break
                        except OSError:
                            continue
                except OSError:
                    pass
        except Exception:
            pass
        await asyncio.sleep(2.0)

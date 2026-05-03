"""Session and agent persistence logic."""
import datetime
import json
import time
from pathlib import Path

from claude_monitor import db
from claude_monitor import config, state


def current_session_id(project_name: str) -> str | None:
    path_str = state._jsonl_cache.get(project_name, {}).get("jsonl_path")
    return Path(path_str).stem if path_str else None


def persist_done_agents(agents_dir: Path, project_name: str,
                        session_id: str | None, now_ts: float) -> list[dict]:
    """Scan agents dir, persist done agents to SQLite, delete old files."""
    persisted = state._persisted_agent_ids.setdefault(project_name, set())
    active = []
    for agent_file in list(agents_dir.glob("agent_*.json")):
        try:
            agent_data = json.loads(agent_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        agent_state = agent_data.get("state")
        agent_id = agent_data.get("id", agent_file.stem)

        if agent_state == "running":
            ts_str = agent_data.get("last_updated") or agent_data.get("started_at", "")
            stale = False
            if ts_str:
                try:
                    ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    stale = (now_ts - ts.timestamp()) > 600
                except Exception:
                    pass
            if not stale:
                active.append(agent_data)

        elif agent_state == "done":
            finished_at = agent_data.get("finished_at", "")
            age = float("inf")
            if finished_at:
                try:
                    ft = datetime.datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
                    age = now_ts - ft.timestamp()
                except Exception:
                    pass

            if agent_id not in persisted:
                try:
                    db.upsert_agent_run(agent_data, project_name, session_id)
                    persisted.add(agent_id)
                except Exception:
                    pass

            if age < 300:
                active.append(agent_data)
            else:
                try:
                    agent_file.unlink(missing_ok=True)
                except Exception:
                    pass

    active.sort(key=lambda a: a.get("started_at", ""))
    return active


def persist_and_clean_session(project_name: str, data: dict, agents_dir: Path | None) -> None:
    """Persist session summary to SQLite and delete all remaining agent files."""
    session_id = current_session_id(project_name)
    if not session_id:
        return

    if agents_dir and agents_dir.is_dir():
        persisted = state._persisted_agent_ids.setdefault(project_name, set())
        for agent_file in list(agents_dir.glob("agent_*.json")):
            try:
                agent_data = json.loads(agent_file.read_text(encoding="utf-8"))
                agent_id = agent_data.get("id", agent_file.stem)
                if agent_id not in persisted:
                    db.upsert_agent_run(agent_data, project_name, session_id)
                    persisted.add(agent_id)
            except Exception:
                pass
            try:
                agent_file.unlink(missing_ok=True)
            except Exception:
                pass
        state._persisted_agent_ids.pop(project_name, None)

    stats = data.get("stats", {})
    agent_count = len(state._persisted_agent_ids.get(project_name, set()))
    finished_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        db.upsert_session_run(session_id, project_name, stats,
                              finished_at=finished_at, agent_count=agent_count)
    except Exception:
        pass


def list_sessions(project_name: str) -> list[dict]:
    """Lists root-level JSONL sessions for a project, newest-first."""
    if project_name not in state._status_paths:
        return []
    project_path = state._status_paths[project_name].parents[1]
    encoded = str(project_path).replace("/", "-")
    jsonl_dir = config.CLAUDE_PROJECTS_DIR / encoded
    if not jsonl_dir.is_dir():
        return []
    now = time.time()
    sessions = []
    try:
        for f in jsonl_dir.glob("*.jsonl"):
            try:
                mtime = f.stat().st_mtime
                is_active = (now - mtime) <= config.JSONL_ACTIVE_SECONDS
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

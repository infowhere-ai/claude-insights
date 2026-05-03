"""Project token stats — reads from JSONL session files with mtime cache."""
import json
from pathlib import Path

from claude_monitor import config, state


def get_project_stats(project_path: Path, project_name: str) -> dict:
    encoded = str(project_path).replace("/", "-")
    jsonl_dir = config.CLAUDE_PROJECTS_DIR / encoded
    if not jsonl_dir.is_dir():
        return {}

    try:
        jsonl_files = list(jsonl_dir.glob("*.jsonl"))
    except OSError:
        return {}
    if not jsonl_files:
        return {}

    latest = max(jsonl_files, key=lambda p: p.stat().st_mtime)
    latest_mtime = latest.stat().st_mtime
    cache_key = str(latest)

    if state._jsonl_mtimes.get(cache_key) == latest_mtime and project_name in state._project_stats_cache:
        return state._project_stats_cache[project_name]

    session_input = 0
    session_output = 0
    session_cache_read = 0
    last_input_tokens = 0
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
        "session_ctx_tokens": last_input_tokens,
        "model": model,
    }
    state._jsonl_mtimes[cache_key] = latest_mtime
    state._project_stats_cache[project_name] = stats
    return stats

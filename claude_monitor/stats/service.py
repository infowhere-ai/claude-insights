"""Project token stats — reads from JSONL session files with mtime cache."""

import json
from pathlib import Path

from claude_monitor import config, state


def _parse_token_entry(entry: dict) -> dict | None:
    """Extract token counts and model from a JSONL assistant entry.

    Returns a dict with keys input, output, cache_read, ctx, model,
    or None if the entry is not an assistant message.
    """
    if entry.get("type") != "assistant" or "message" not in entry:
        return None
    msg = entry["message"]
    u = msg.get("usage", {})
    input_tokens = u.get("input_tokens", 0)
    cache_read = u.get("cache_read_input_tokens", 0)
    cache_create = u.get("cache_creation_input_tokens", 0)
    return {
        "input": input_tokens,
        "output": u.get("output_tokens", 0),
        "cache_read": cache_read,
        "ctx": input_tokens + cache_read + cache_create,
        "model": msg.get("model", ""),
    }


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

    if (
        state._jsonl_mtimes.get(cache_key) == latest_mtime
        and project_name in state._project_stats_cache
    ):
        return state._project_stats_cache[project_name]

    session_input = session_output = session_cache_read = last_ctx = 0
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
                tokens = _parse_token_entry(d)
                if tokens is None:
                    continue
                session_input += tokens["input"]
                session_output += tokens["output"]
                session_cache_read += tokens["cache_read"]
                last_ctx = tokens["ctx"]
                if tokens["model"]:
                    model = tokens["model"]
    except OSError:
        return {}

    stats = {
        "session_input_tokens": session_input,
        "session_output_tokens": session_output,
        "session_cache_read": session_cache_read,
        "session_ctx_tokens": last_ctx,
        "model": model,
    }
    state._jsonl_mtimes[cache_key] = latest_mtime
    state._project_stats_cache[project_name] = stats
    return stats

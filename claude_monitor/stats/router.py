"""Stats endpoints — insights, weekly, usage window."""

import json
import time

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from claude_monitor import config, state

router = APIRouter(tags=["stats"])


@router.get("/api/weekly-stats")
async def get_weekly_stats():
    result = {}
    for name, path in state._status_paths.items():
        weekly_file = path.parent / "weekly_tokens.json"
        try:
            if weekly_file.exists():
                result[name] = json.loads(weekly_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"weekly": result}


@router.get("/api/insights-stats")
async def get_insights_stats(project: str = Query(...)):
    if project not in state._status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    project_path = state._status_paths[project].parents[1]
    encoded = str(project_path).replace("/", "-")
    jsonl_dir = config.CLAUDE_PROJECTS_DIR / encoded
    if not jsonl_dir.is_dir():
        return {
            "sessions_count": 0,
            "sessions_7d": 0,
            "total_tokens": 0,
            "cache_hit_pct": 0,
            "top_tool": None,
            "top_tool_count": 0,
        }
    cutoff = time.time() - 7 * 24 * 3600
    sessions_total = 0
    sessions_count = 0
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
                            total_input += u.get("input_tokens", 0)
                            total_output += u.get("output_tokens", 0)
                            total_cache += u.get("cache_read_input_tokens", 0)
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
    total_real = total_input + total_cache
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


@router.get("/api/usage-window")
async def get_usage_window(project: str = Query(...)):
    WINDOW_SECS = 5 * 3600
    if project not in state._status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    project_path = state._status_paths[project].parents[1]
    encoded = str(project_path).replace("/", "-")
    jsonl_dir = config.CLAUDE_PROJECTS_DIR / encoded
    if not jsonl_dir.is_dir():
        return {
            "window_tokens": 0,
            "window_start": None,
            "window_end": None,
            "sessions_in_window": 0,
        }

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

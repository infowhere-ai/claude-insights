"""Account info endpoint."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter

from claude_monitor import config

router = APIRouter(tags=["account"])


def _read_settings(settings_file: Path) -> dict:
    """Read and parse settings.json, returning {} on any error."""
    try:
        if settings_file.exists():
            return json.loads(settings_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _read_daily_activity(cache_file: Path) -> list:
    """Read stats-cache.json and return the dailyActivity list, or [] on any error."""
    try:
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8")).get("dailyActivity", [])
    except Exception:
        pass
    return []


def _sum_tokens_from_file(f: Path, week_ago: datetime) -> tuple[dict, str] | None:
    """Process one JSONL file and return (partial_totals, tier) or None if skipped/error."""
    try:
        if datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) < week_ago:
            return None
        totals = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}
        tier: str = "standard"
        with f.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") == "assistant" and "message" in d:
                    u = d["message"].get("usage", {})
                    totals["input"] += u.get("input_tokens", 0)
                    totals["output"] += u.get("output_tokens", 0)
                    totals["cache_creation"] += u.get("cache_creation_input_tokens", 0)
                    totals["cache_read"] += u.get("cache_read_input_tokens", 0)
                    if u.get("service_tier"):
                        tier = u["service_tier"]
        return totals, tier
    except Exception:
        return None


def _sum_tokens_from_jsonl(projects_dir: Path, week_ago: datetime) -> tuple[dict, str]:
    """Iterate JSONL files under projects_dir modified after week_ago.

    Returns (token_totals, service_tier).
    """
    token_totals = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}
    service_tier: str = "standard"

    if not projects_dir.is_dir():
        return token_totals, service_tier

    for jsonl_file in projects_dir.rglob("*.jsonl"):
        result = _sum_tokens_from_file(jsonl_file, week_ago)
        if result is None:
            continue
        partial, tier = result
        for key in token_totals:
            token_totals[key] += partial[key]
        if tier != "standard":
            service_tier = tier

    return token_totals, service_tier


def _get_account_sync() -> dict:
    """Synchronous worker — runs in a thread via asyncio.to_thread()."""
    settings = _read_settings(config.CLAUDE_SETTINGS_FILE)
    daily_activity = _read_daily_activity(config.CLAUDE_STATS_CACHE)
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    token_totals, service_tier = _sum_tokens_from_jsonl(config.CLAUDE_PROJECTS_DIR, week_ago)

    return {
        "model": settings.get("model", "unknown"),
        "enabled_plugins": list((settings.get("enabledPlugins") or {}).keys()),
        "daily_activity": daily_activity,
        "tokens_week": token_totals,
        "service_tier": service_tier,
    }


@router.get("/api/account")
async def get_account():
    return await asyncio.to_thread(_get_account_sync)

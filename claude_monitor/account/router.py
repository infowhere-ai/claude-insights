"""Account info endpoint."""
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(tags=["account"])


def _get_account_sync() -> dict:
    """Synchronous worker — runs in a thread via asyncio.to_thread()."""
    home = Path.home()

    settings: dict = {}
    try:
        sp = home / ".claude" / "settings.json"
        if sp.exists():
            settings = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        pass

    daily_activity: list = []
    try:
        sc = home / ".claude" / "stats-cache.json"
        if sc.exists():
            daily_activity = json.loads(sc.read_text(encoding="utf-8")).get("dailyActivity", [])
    except Exception:
        pass

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


@router.get("/api/account")
async def get_account():
    return await asyncio.to_thread(_get_account_sync)

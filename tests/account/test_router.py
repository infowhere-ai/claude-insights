"""Tests for account endpoint."""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from claude_monitor.account import router as account_router
from claude_monitor import config


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


def test_account_returns_expected_structure(app_client):
    mock_data = {
        "model": "claude-sonnet-4-6",
        "enabled_plugins": [],
        "daily_activity": [],
        "tokens_week": {"input": 5000, "output": 2000, "cache_creation": 100, "cache_read": 800},
        "service_tier": "standard",
    }
    with patch.object(account_router, "_get_account_sync", return_value=mock_data):
        r = app_client.get("/api/account")
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "claude-sonnet-4-6"
    assert "tokens_week" in body
    assert body["tokens_week"]["input"] == 5000


def test_account_sync_reads_settings(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"model": "claude-opus-4-7", "enabledPlugins": {"mcp-tool": True}}),
    )
    with patch.object(config, "CLAUDE_SETTINGS_FILE", settings_file), \
         patch.object(config, "CLAUDE_STATS_CACHE", tmp_path / "no-cache.json"), \
         patch.object(config, "CLAUDE_PROJECTS_DIR", tmp_path / "projects"):
        result = account_router._get_account_sync()
    assert result["model"] == "claude-opus-4-7"
    assert "mcp-tool" in result["enabled_plugins"]


def test_account_sync_handles_missing_settings(tmp_path):
    with patch.object(config, "CLAUDE_SETTINGS_FILE", tmp_path / "no-settings.json"), \
         patch.object(config, "CLAUDE_STATS_CACHE", tmp_path / "no-cache.json"), \
         patch.object(config, "CLAUDE_PROJECTS_DIR", tmp_path / "projects"):
        result = account_router._get_account_sync()
    assert result["model"] == "unknown"
    assert result["tokens_week"]["input"] == 0


def test_account_sync_aggregates_tokens(tmp_path):
    projects_dir = tmp_path / "projects" / "my-proj"
    projects_dir.mkdir(parents=True)
    jsonl_file = projects_dir / "sess.jsonl"
    _write_jsonl(jsonl_file, [{
        "type": "assistant",
        "message": {
            "usage": {
                "input_tokens": 300,
                "output_tokens": 150,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 50,
                "service_tier": "priority",
            }
        }
    }])
    with patch.object(config, "CLAUDE_SETTINGS_FILE", tmp_path / "no-settings.json"), \
         patch.object(config, "CLAUDE_STATS_CACHE", tmp_path / "no-cache.json"), \
         patch.object(config, "CLAUDE_PROJECTS_DIR", tmp_path / "projects"):
        result = account_router._get_account_sync()
    assert result["tokens_week"]["input"] == 300
    assert result["tokens_week"]["output"] == 150
    assert result["service_tier"] == "priority"


def test_account_sync_reads_stats_cache(tmp_path):
    cache_file = tmp_path / "stats-cache.json"
    cache_file.write_text(
        json.dumps({"dailyActivity": [{"date": "2026-01-01", "messages": 10}]})
    )
    with patch.object(config, "CLAUDE_SETTINGS_FILE", tmp_path / "no-settings.json"), \
         patch.object(config, "CLAUDE_STATS_CACHE", cache_file), \
         patch.object(config, "CLAUDE_PROJECTS_DIR", tmp_path / "projects"):
        result = account_router._get_account_sync()
    assert len(result["daily_activity"]) == 1
    assert result["daily_activity"][0]["date"] == "2026-01-01"


def test_account_sync_skips_old_jsonl(tmp_path):
    projects_dir = tmp_path / "projects" / "old-proj"
    projects_dir.mkdir(parents=True)
    old_f = projects_dir / "old.jsonl"
    _write_jsonl(old_f, [{"type": "assistant", "message": {"usage": {"input_tokens": 999}}}])
    old_time = time.time() - 8 * 24 * 3600
    os.utime(old_f, (old_time, old_time))
    with patch.object(config, "CLAUDE_SETTINGS_FILE", tmp_path / "no-settings.json"), \
         patch.object(config, "CLAUDE_STATS_CACHE", tmp_path / "no-cache.json"), \
         patch.object(config, "CLAUDE_PROJECTS_DIR", tmp_path / "projects"):
        result = account_router._get_account_sync()
    assert result["tokens_week"]["input"] == 0

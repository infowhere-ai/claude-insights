"""Tests for account endpoint."""

import json
import os
import sys
import time
from datetime import datetime, timedelta
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
    with (
        patch.object(config, "CLAUDE_SETTINGS_FILE", settings_file),
        patch.object(config, "CLAUDE_STATS_CACHE", tmp_path / "no-cache.json"),
        patch.object(config, "CLAUDE_PROJECTS_DIR", tmp_path / "projects"),
    ):
        result = account_router._get_account_sync()
    assert result["model"] == "claude-opus-4-7"
    assert "mcp-tool" in result["enabled_plugins"]


def test_account_sync_handles_missing_settings(tmp_path):
    with (
        patch.object(config, "CLAUDE_SETTINGS_FILE", tmp_path / "no-settings.json"),
        patch.object(config, "CLAUDE_STATS_CACHE", tmp_path / "no-cache.json"),
        patch.object(config, "CLAUDE_PROJECTS_DIR", tmp_path / "projects"),
    ):
        result = account_router._get_account_sync()
    assert result["model"] == "unknown"
    assert result["tokens_week"]["input"] == 0


def test_account_sync_aggregates_tokens(tmp_path):
    projects_dir = tmp_path / "projects" / "my-proj"
    projects_dir.mkdir(parents=True)
    jsonl_file = projects_dir / "sess.jsonl"
    _write_jsonl(
        jsonl_file,
        [
            {
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 300,
                        "output_tokens": 150,
                        "cache_creation_input_tokens": 10,
                        "cache_read_input_tokens": 50,
                        "service_tier": "priority",
                    }
                },
            }
        ],
    )
    with (
        patch.object(config, "CLAUDE_SETTINGS_FILE", tmp_path / "no-settings.json"),
        patch.object(config, "CLAUDE_STATS_CACHE", tmp_path / "no-cache.json"),
        patch.object(config, "CLAUDE_PROJECTS_DIR", tmp_path / "projects"),
    ):
        result = account_router._get_account_sync()
    assert result["tokens_week"]["input"] == 300
    assert result["tokens_week"]["output"] == 150
    assert result["service_tier"] == "priority"


def test_account_sync_reads_stats_cache(tmp_path):
    cache_file = tmp_path / "stats-cache.json"
    cache_file.write_text(json.dumps({"dailyActivity": [{"date": "2026-01-01", "messages": 10}]}))
    with (
        patch.object(config, "CLAUDE_SETTINGS_FILE", tmp_path / "no-settings.json"),
        patch.object(config, "CLAUDE_STATS_CACHE", cache_file),
        patch.object(config, "CLAUDE_PROJECTS_DIR", tmp_path / "projects"),
    ):
        result = account_router._get_account_sync()
    assert len(result["daily_activity"]) == 1
    assert result["daily_activity"][0]["date"] == "2026-01-01"


class TestReadSettings:
    def test_reads_model_and_plugins(self, tmp_path):
        """_read_settings returns parsed dict from existing file."""
        f = tmp_path / "settings.json"
        f.write_text(json.dumps({"model": "claude-opus", "enabledPlugins": {"tool-a": True}}))
        result = account_router._read_settings(f)
        assert result["model"] == "claude-opus"
        assert "enabledPlugins" in result

    def test_returns_empty_dict_on_missing_file(self, tmp_path):
        """_read_settings returns {} when file does not exist."""
        result = account_router._read_settings(tmp_path / "no-settings.json")
        assert result == {}

    def test_returns_empty_dict_on_invalid_json(self, tmp_path):
        """_read_settings returns {} when JSON is malformed."""
        f = tmp_path / "settings.json"
        f.write_text("not json {{{")
        result = account_router._read_settings(f)
        assert result == {}


class TestReadDailyActivity:
    def test_reads_daily_activity_from_cache(self, tmp_path):
        """_read_daily_activity returns the dailyActivity list."""
        f = tmp_path / "stats-cache.json"
        f.write_text(json.dumps({"dailyActivity": [{"date": "2026-01-01", "count": 5}]}))
        result = account_router._read_daily_activity(f)
        assert len(result) == 1
        assert result[0]["date"] == "2026-01-01"

    def test_returns_empty_list_on_missing_file(self, tmp_path):
        """_read_daily_activity returns [] when file does not exist."""
        result = account_router._read_daily_activity(tmp_path / "no-cache.json")
        assert result == []

    def test_returns_empty_list_on_invalid_json(self, tmp_path):
        """_read_daily_activity returns [] when JSON is malformed."""
        f = tmp_path / "stats-cache.json"
        f.write_text("not json")
        result = account_router._read_daily_activity(f)
        assert result == []

    def test_returns_empty_list_when_key_missing(self, tmp_path):
        """_read_daily_activity returns [] when dailyActivity key is absent."""
        f = tmp_path / "stats-cache.json"
        f.write_text(json.dumps({"otherKey": 42}))
        result = account_router._read_daily_activity(f)
        assert result == []


class TestSumTokensFromFile:
    def _write_jsonl(self, path: Path, entries: list[dict]) -> None:
        path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

    def test_returns_partial_totals_for_recent_file(self, tmp_path):
        """
        Given a recent JSONL file with assistant usage entries
        When _sum_tokens_from_file is called
        Then it returns (partial_totals_dict, tier) with the correct values
        """
        from claude_monitor.account.router import _sum_tokens_from_file

        f = tmp_path / "sess.jsonl"
        self._write_jsonl(
            f,
            [
                {
                    "type": "assistant",
                    "message": {
                        "usage": {
                            "input_tokens": 200,
                            "output_tokens": 100,
                            "cache_creation_input_tokens": 10,
                            "cache_read_input_tokens": 30,
                            "service_tier": "priority",
                        }
                    },
                }
            ],
        )
        week_ago = datetime.now() - timedelta(days=7)
        result = _sum_tokens_from_file(f, week_ago)
        assert result is not None
        totals, tier = result
        assert totals["input"] == 200
        assert totals["output"] == 100
        assert totals["cache_creation"] == 10
        assert totals["cache_read"] == 30
        assert tier == "priority"

    def test_returns_none_for_old_file(self, tmp_path):
        """
        Given a JSONL file older than week_ago
        When _sum_tokens_from_file is called
        Then it returns None
        """
        from claude_monitor.account.router import _sum_tokens_from_file

        f = tmp_path / "old.jsonl"
        self._write_jsonl(f, [{"type": "assistant", "message": {"usage": {"input_tokens": 999}}}])
        import time as time_mod

        old_time = time_mod.time() - 8 * 24 * 3600
        import os

        os.utime(f, (old_time, old_time))
        week_ago = datetime.now() - timedelta(days=7)
        result = _sum_tokens_from_file(f, week_ago)
        assert result is None

    def test_returns_none_on_oserror(self, tmp_path):
        """
        Given a non-existent JSONL file
        When _sum_tokens_from_file is called
        Then it returns None
        """
        from claude_monitor.account.router import _sum_tokens_from_file

        week_ago = datetime.now() - timedelta(days=7)
        result = _sum_tokens_from_file(tmp_path / "ghost.jsonl", week_ago)
        assert result is None

    def test_returns_default_tier_when_no_service_tier(self, tmp_path):
        """
        Given a JSONL file with no service_tier field
        When _sum_tokens_from_file is called
        Then tier in result is 'standard'
        """
        from claude_monitor.account.router import _sum_tokens_from_file

        f = tmp_path / "no_tier.jsonl"
        self._write_jsonl(f, [{"type": "assistant", "message": {"usage": {"input_tokens": 5}}}])
        week_ago = datetime.now() - timedelta(days=7)
        result = _sum_tokens_from_file(f, week_ago)
        assert result is not None
        _, tier = result
        assert tier == "standard"


class TestSumTokensFromJsonl:
    def _write_jsonl(self, path: Path, entries: list[dict]) -> None:
        path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

    def test_sums_tokens_from_recent_files(self, tmp_path):
        """_sum_tokens_from_jsonl aggregates tokens from files modified within a week."""
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        self._write_jsonl(
            proj_dir / "sess.jsonl",
            [
                {
                    "type": "assistant",
                    "message": {
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "cache_creation_input_tokens": 5,
                            "cache_read_input_tokens": 20,
                            "service_tier": "priority",
                        }
                    },
                }
            ],
        )
        week_ago = datetime.now() - timedelta(days=7)
        totals, tier = account_router._sum_tokens_from_jsonl(tmp_path, week_ago)
        assert totals["input"] == 100
        assert totals["output"] == 50
        assert totals["cache_creation"] == 5
        assert totals["cache_read"] == 20
        assert tier == "priority"

    def test_skips_files_older_than_week(self, tmp_path):
        """_sum_tokens_from_jsonl ignores files modified more than 7 days ago."""
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        old_file = proj_dir / "old.jsonl"
        self._write_jsonl(
            old_file,
            [{"type": "assistant", "message": {"usage": {"input_tokens": 999}}}],
        )
        old_time = time.time() - 8 * 24 * 3600
        os.utime(old_file, (old_time, old_time))
        week_ago = datetime.now() - timedelta(days=7)
        totals, tier = account_router._sum_tokens_from_jsonl(tmp_path, week_ago)
        assert totals["input"] == 0

    def test_returns_default_tier_when_no_service_tier(self, tmp_path):
        """_sum_tokens_from_jsonl returns 'standard' when no service_tier present."""
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        self._write_jsonl(
            proj_dir / "sess.jsonl",
            [{"type": "assistant", "message": {"usage": {"input_tokens": 10}}}],
        )
        week_ago = datetime.now() - timedelta(days=7)
        _, tier = account_router._sum_tokens_from_jsonl(tmp_path, week_ago)
        assert tier == "standard"

    def test_returns_empty_totals_when_projects_dir_missing(self, tmp_path):
        """_sum_tokens_from_jsonl returns zero totals when projects dir does not exist."""
        week_ago = datetime.now() - timedelta(days=7)
        totals, tier = account_router._sum_tokens_from_jsonl(tmp_path / "no-dir", week_ago)
        assert totals["input"] == 0
        assert tier == "standard"


def test_account_sync_skips_old_jsonl(tmp_path):
    projects_dir = tmp_path / "projects" / "old-proj"
    projects_dir.mkdir(parents=True)
    old_f = projects_dir / "old.jsonl"
    _write_jsonl(old_f, [{"type": "assistant", "message": {"usage": {"input_tokens": 999}}}])
    old_time = time.time() - 8 * 24 * 3600
    os.utime(old_f, (old_time, old_time))
    with (
        patch.object(config, "CLAUDE_SETTINGS_FILE", tmp_path / "no-settings.json"),
        patch.object(config, "CLAUDE_STATS_CACHE", tmp_path / "no-cache.json"),
        patch.object(config, "CLAUDE_PROJECTS_DIR", tmp_path / "projects"),
    ):
        result = account_router._get_account_sync()
    assert result["tokens_week"]["input"] == 0

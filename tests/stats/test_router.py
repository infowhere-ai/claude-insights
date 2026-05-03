"""Tests for stats endpoints."""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


def _assistant_entry(tool: str = "Read", input_tokens: int = 100,
                     output_tokens: int = 50) -> dict:
    return {
        "type": "assistant",
        "timestamp": "2026-01-01T10:00:00Z",
        "message": {
            "model": "claude-sonnet-4-6",
            "content": [{"type": "tool_use", "id": "t1", "name": tool, "input": {}}],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 0,
            },
        },
    }


def test_weekly_stats_returns_dict(app_client):
    r = app_client.get("/api/weekly-stats")
    assert r.status_code == 200
    assert "weekly" in r.json()


def test_weekly_stats_with_data(app_client, tmp_project):
    from claude_monitor import state
    weekly_data = {"total_input": 1000, "total_output": 500}
    weekly_file = tmp_project / ".claude" / "weekly_tokens.json"
    weekly_file.write_text(json.dumps(weekly_data))
    state._status_paths["my-project"] = tmp_project / ".claude" / "status.json"

    r = app_client.get("/api/weekly-stats")
    assert r.status_code == 200
    assert "weekly" in r.json()


def test_insights_stats_unknown_project_returns_404(app_client):
    r = app_client.get("/api/insights-stats?project=does-not-exist")
    assert r.status_code == 404


def test_usage_window_unknown_project_returns_404(app_client):
    r = app_client.get("/api/usage-window?project=does-not-exist")
    assert r.status_code == 404


class TestInsightsStatsEndpoint:
    def _setup_jsonl_dir(self, tmp_path, project_path: Path) -> Path:
        encoded = str(project_path).replace("/", "-")
        jsonl_dir = tmp_path / "claude_p" / encoded
        jsonl_dir.mkdir(parents=True)
        return jsonl_dir

    def test_insights_stats_with_data(self, app_client, tmp_project, tmp_path):
        from claude_monitor import config as config_module
        jsonl_dir = self._setup_jsonl_dir(tmp_path, tmp_project)
        f = jsonl_dir / "sess.jsonl"
        _write_jsonl(f, [
            _assistant_entry("Read", input_tokens=200, output_tokens=100),
            _assistant_entry("Write", input_tokens=150, output_tokens=80),
        ])
        with patch.object(config_module, "CLAUDE_PROJECTS_DIR", tmp_path / "claude_p"):
            r = app_client.get("/api/insights-stats?project=my-project")
        assert r.status_code == 200
        body = r.json()
        assert body["sessions_count"] >= 1
        assert body["total_tokens"] > 0

    def test_insights_stats_no_jsonl_dir(self, app_client, tmp_project, tmp_path):
        from claude_monitor import config as config_module
        with patch.object(config_module, "CLAUDE_PROJECTS_DIR", tmp_path / "nonexistent"):
            r = app_client.get("/api/insights-stats?project=my-project")
        assert r.status_code == 200
        body = r.json()
        assert body["sessions_count"] == 0
        assert body["total_tokens"] == 0

    def test_insights_stats_calculates_top_tool(self, app_client, tmp_project, tmp_path):
        from claude_monitor import config as config_module
        jsonl_dir = self._setup_jsonl_dir(tmp_path, tmp_project)
        entries = [_assistant_entry("Read")] * 3 + [_assistant_entry("Write")]
        _write_jsonl(jsonl_dir / "sess.jsonl", entries)
        with patch.object(config_module, "CLAUDE_PROJECTS_DIR", tmp_path / "claude_p"):
            r = app_client.get("/api/insights-stats?project=my-project")
        assert r.json()["top_tool"] == "Read"
        assert r.json()["top_tool_count"] == 3


class TestUsageWindowEndpoint:
    def test_usage_window_with_data(self, app_client, tmp_project, tmp_path):
        from claude_monitor import config as config_module
        encoded = str(tmp_project).replace("/", "-")
        jsonl_dir = tmp_path / "claude_uw" / encoded
        jsonl_dir.mkdir(parents=True)
        f = jsonl_dir / "sess.jsonl"
        _write_jsonl(f, [_assistant_entry("Read", input_tokens=500, output_tokens=200)])
        with patch.object(config_module, "CLAUDE_PROJECTS_DIR", tmp_path / "claude_uw"):
            r = app_client.get("/api/usage-window?project=my-project")
        assert r.status_code == 200
        body = r.json()
        assert body["window_tokens"] > 0
        assert body["sessions_in_window"] == 1
        assert "remaining_secs" in body
        assert "elapsed_pct" in body

    def test_usage_window_no_jsonl_dir(self, app_client, tmp_project, tmp_path):
        from claude_monitor import config as config_module
        with patch.object(config_module, "CLAUDE_PROJECTS_DIR", tmp_path / "nonexistent"):
            r = app_client.get("/api/usage-window?project=my-project")
        assert r.status_code == 200
        body = r.json()
        assert body["window_tokens"] == 0
        assert body["sessions_in_window"] == 0

    def test_usage_window_ignores_old_sessions(self, app_client, tmp_project, tmp_path):
        from claude_monitor import config as config_module
        encoded = str(tmp_project).replace("/", "-")
        jsonl_dir = tmp_path / "claude_uw2" / encoded
        jsonl_dir.mkdir(parents=True)
        f = jsonl_dir / "old.jsonl"
        _write_jsonl(f, [_assistant_entry("Read", input_tokens=999)])
        old_time = time.time() - 6 * 3600
        os.utime(f, (old_time, old_time))
        with patch.object(config_module, "CLAUDE_PROJECTS_DIR", tmp_path / "claude_uw2"):
            r = app_client.get("/api/usage-window?project=my-project")
        assert r.json()["sessions_in_window"] == 0

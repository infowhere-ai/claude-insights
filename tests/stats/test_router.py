"""Tests for stats endpoints."""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from claude_monitor.stats.router import _scan_jsonl_for_stats, _scan_jsonl_for_window_tokens


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


# ── _scan_jsonl_for_stats ─────────────────────────────────────────────────────


class TestScanJsonlForStats:
    def test_returns_none_for_old_file(self, tmp_path):
        f = tmp_path / "old.jsonl"
        _write_jsonl(f, [{"type": "assistant", "message": {"usage": {"input_tokens": 10}}}])
        old_time = time.time() - 10 * 24 * 3600
        os.utime(f, (old_time, old_time))
        cutoff = time.time() - 7 * 24 * 3600
        result = _scan_jsonl_for_stats(f, cutoff)
        assert result is None

    def test_returns_skipped_true_for_old_file(self, tmp_path):
        f = tmp_path / "old2.jsonl"
        _write_jsonl(f, [])
        old_time = time.time() - 10 * 24 * 3600
        os.utime(f, (old_time, old_time))
        cutoff = time.time() - 7 * 24 * 3600
        result = _scan_jsonl_for_stats(f, cutoff)
        assert result is None

    def test_returns_token_counts_for_recent_file(self, tmp_path):
        f = tmp_path / "recent.jsonl"
        _write_jsonl(
            f,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "name": "Read"}],
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "cache_read_input_tokens": 20,
                        },
                    },
                }
            ],
        )
        cutoff = time.time() - 7 * 24 * 3600
        result = _scan_jsonl_for_stats(f, cutoff)
        assert result is not None
        assert result["input"] == 100
        assert result["output"] == 50
        assert result["cache"] == 20

    def test_counts_tools_from_content(self, tmp_path):
        f = tmp_path / "tools.jsonl"
        _write_jsonl(
            f,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Read"},
                            {"type": "tool_use", "name": "Read"},
                            {"type": "tool_use", "name": "Bash"},
                        ],
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "cache_read_input_tokens": 0,
                        },
                    },
                }
            ],
        )
        cutoff = time.time() - 7 * 24 * 3600
        result = _scan_jsonl_for_stats(f, cutoff)
        assert result is not None
        assert result["tools"]["Read"] == 2
        assert result["tools"]["Bash"] == 1

    def test_returns_none_on_oserror(self, tmp_path):
        cutoff = time.time() - 7 * 24 * 3600
        result = _scan_jsonl_for_stats(tmp_path / "nonexistent.jsonl", cutoff)
        assert result is None

    def test_skips_non_assistant_entries(self, tmp_path):
        f = tmp_path / "mixed.jsonl"
        _write_jsonl(
            f,
            [
                {
                    "type": "user",
                    "message": {
                        "usage": {"input_tokens": 999, "output_tokens": 999},
                        "content": [],
                    },
                }
            ],
        )
        cutoff = time.time() - 7 * 24 * 3600
        result = _scan_jsonl_for_stats(f, cutoff)
        assert result is not None
        assert result["input"] == 0
        assert result["output"] == 0


# ── _scan_jsonl_for_window_tokens ─────────────────────────────────────────────


class TestScanJsonlForWindowTokens:
    def test_returns_none_for_old_file(self, tmp_path):
        f = tmp_path / "old.jsonl"
        _write_jsonl(f, [{"type": "assistant", "message": {"usage": {"input_tokens": 10}}}])
        old_time = time.time() - 6 * 3600
        os.utime(f, (old_time, old_time))
        cutoff = time.time() - 5 * 3600
        result = _scan_jsonl_for_window_tokens(f, cutoff)
        assert result is None

    def test_returns_tokens_and_mtime_for_recent_file(self, tmp_path):
        f = tmp_path / "recent.jsonl"
        _write_jsonl(
            f,
            [
                {
                    "type": "assistant",
                    "message": {
                        "usage": {"input_tokens": 300, "output_tokens": 150},
                        "content": [],
                    },
                }
            ],
        )
        cutoff = time.time() - 5 * 3600
        result = _scan_jsonl_for_window_tokens(f, cutoff)
        assert result is not None
        assert result["tokens"] == 450
        assert "mtime" in result

    def test_returns_none_on_oserror(self, tmp_path):
        cutoff = time.time() - 5 * 3600
        result = _scan_jsonl_for_window_tokens(tmp_path / "ghost.jsonl", cutoff)
        assert result is None

    def test_skips_non_assistant_entries(self, tmp_path):
        f = tmp_path / "user_only.jsonl"
        _write_jsonl(
            f,
            [
                {
                    "type": "user",
                    "message": {"usage": {"input_tokens": 999}, "content": []},
                }
            ],
        )
        cutoff = time.time() - 5 * 3600
        result = _scan_jsonl_for_window_tokens(f, cutoff)
        assert result is not None
        assert result["tokens"] == 0


def _assistant_entry(tool: str = "Read", input_tokens: int = 100, output_tokens: int = 50) -> dict:
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


class TestAggregateSessionStats:
    """Tests for _aggregate_session_stats helper (extracted from get_insights_stats)."""

    def _setup_jsonl_dir(self, tmp_path: Path) -> Path:
        encoded = str(tmp_path / "proj").replace("/", "-")
        d = tmp_path / "claude_p" / encoded
        d.mkdir(parents=True)
        return d

    def test_returns_zero_totals_for_empty_dir(self, tmp_path):
        """
        Given a JSONL directory with no files
        When _aggregate_session_stats is called
        Then all counters are zero and tool_counts is empty
        """
        from claude_monitor.stats.router import _aggregate_session_stats

        jsonl_dir = tmp_path / "empty_dir"
        jsonl_dir.mkdir()
        cutoff = time.time() - 7 * 24 * 3600
        result = _aggregate_session_stats(jsonl_dir, cutoff)

        assert result["sessions_total"] == 0
        assert result["sessions_count"] == 0
        assert result["total_input"] == 0
        assert result["total_output"] == 0
        assert result["total_cache"] == 0
        assert result["tool_counts"] == {}

    def test_aggregates_recent_sessions(self, tmp_path):
        """
        Given a JSONL dir with one recent file containing assistant entries
        When _aggregate_session_stats is called
        Then sessions_count=1 and totals reflect the file
        """
        from claude_monitor.stats.router import _aggregate_session_stats

        jsonl_dir = tmp_path / "recent"
        jsonl_dir.mkdir()
        f = jsonl_dir / "sess.jsonl"
        f.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "name": "Read"}],
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "cache_read_input_tokens": 20,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        cutoff = time.time() - 7 * 24 * 3600
        result = _aggregate_session_stats(jsonl_dir, cutoff)

        assert result["sessions_total"] == 1
        assert result["sessions_count"] == 1
        assert result["total_input"] == 100
        assert result["total_output"] == 50
        assert result["total_cache"] == 20
        assert result["tool_counts"].get("Read") == 1

    def test_sessions_total_includes_old_files(self, tmp_path):
        """
        Given one old and one recent JSONL file
        When _aggregate_session_stats is called
        Then sessions_total=2 but sessions_count=1
        """
        from claude_monitor.stats.router import _aggregate_session_stats

        jsonl_dir = tmp_path / "mixed"
        jsonl_dir.mkdir()
        recent = jsonl_dir / "recent.jsonl"
        recent.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [],
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "cache_read_input_tokens": 0,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        old = jsonl_dir / "old.jsonl"
        old.write_text(
            json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 999}}}),
            encoding="utf-8",
        )
        import os

        old_time = time.time() - 10 * 24 * 3600
        os.utime(old, (old_time, old_time))

        cutoff = time.time() - 7 * 24 * 3600
        result = _aggregate_session_stats(jsonl_dir, cutoff)

        assert result["sessions_total"] == 2
        assert result["sessions_count"] == 1


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
        _write_jsonl(
            f,
            [
                _assistant_entry("Read", input_tokens=200, output_tokens=100),
                _assistant_entry("Write", input_tokens=150, output_tokens=80),
            ],
        )
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

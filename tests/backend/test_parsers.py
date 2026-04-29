"""Tests for JSONL parsing functions using tmp_path fixtures.

Covers _parse_jsonl_tail, _detect_latest_thinking, _get_project_stats,
_parse_session_detail, _list_sessions, _get_latest_jsonl, _get_jsonl_dir.
"""

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import app


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


def _assistant_entry(tool_name: str = "Read", ts: str = "2026-01-01T10:00:00Z",
                     input_tokens: int = 100, output_tokens: int = 50,
                     model: str = "claude-sonnet-4-6", thinking: str = "") -> dict:
    content: list[dict] = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})
    content.append({"type": "tool_use", "id": "tu_1", "name": tool_name, "input": {}})
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "model": model,
            "content": content,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 5,
            },
        },
    }


def _user_entry(cwd: str = "/home/user/project", ts: str = "2026-01-01T10:00:01Z") -> dict:
    return {"type": "user", "timestamp": ts, "cwd": cwd, "message": {"content": []}}


# ── _get_jsonl_dir ─────────────────────────────────────────────────────────────

class TestGetJsonlDir:
    def test_encodes_slashes(self, tmp_path):
        project = tmp_path / "my-project"
        result = app._get_jsonl_dir(project)
        assert "-" in str(result.name)
        assert "/" not in result.name

    def test_returns_path_under_claude_projects(self, tmp_path):
        project = tmp_path / "proj"
        result = app._get_jsonl_dir(project)
        assert result.parent == app.CLAUDE_PROJECTS_DIR


# ── _get_latest_jsonl ──────────────────────────────────────────────────────────

class TestGetLatestJsonl:
    def test_returns_none_when_dir_missing(self, tmp_path):
        path, mtime = app._get_latest_jsonl(tmp_path / "nonexistent")
        assert path is None
        assert mtime == 0.0

    def test_returns_none_when_no_jsonl_files(self, tmp_path):
        jsonl_dir = tmp_path / "encoded"
        jsonl_dir.mkdir()
        with patch.object(app, "CLAUDE_PROJECTS_DIR", tmp_path):
            with patch.object(app, "_get_jsonl_dir", return_value=jsonl_dir):
                path, mtime = app._get_latest_jsonl(tmp_path / "proj")
        assert path is None

    def test_returns_most_recent_file(self, tmp_path):
        jsonl_dir = tmp_path / "encoded"
        jsonl_dir.mkdir()
        older = jsonl_dir / "old.jsonl"
        newer = jsonl_dir / "new.jsonl"
        older.write_text("{}")
        time.sleep(0.01)
        newer.write_text("{}")

        with patch.object(app, "_get_jsonl_dir", return_value=jsonl_dir):
            path, mtime = app._get_latest_jsonl(tmp_path / "proj")

        assert path == newer
        assert mtime > 0.0

    def test_handles_oserror_gracefully(self, tmp_path):
        mock_dir = MagicMock(spec=Path)
        mock_dir.is_dir.return_value = True
        mock_dir.glob.side_effect = OSError("permission denied")
        with patch.object(app, "_get_jsonl_dir", return_value=mock_dir):
            path, mtime = app._get_latest_jsonl(tmp_path / "proj")
        assert path is None
        assert mtime == 0.0


# ── _parse_jsonl_tail ──────────────────────────────────────────────────────────

class TestParseJsonlTail:
    def test_extracts_tool_and_cwd(self, tmp_path):
        f = tmp_path / "session.jsonl"
        _write_jsonl(f, [_user_entry("/my/project"), _assistant_entry("Bash")])
        result = app._parse_jsonl_tail(f)
        assert result["tool"] == "Bash"
        assert result["cwd"] == "/my/project"

    def test_returns_empty_on_missing_file(self, tmp_path):
        result = app._parse_jsonl_tail(tmp_path / "missing.jsonl")
        assert result == {}

    def test_returns_empty_on_invalid_json(self, tmp_path):
        f = tmp_path / "bad.jsonl"
        f.write_text("not json\nalso not json\n")
        result = app._parse_jsonl_tail(f)
        # All JSON lines fail to parse — tool and cwd remain None
        assert result.get("tool") is None
        assert result.get("cwd") is None

    def test_handles_no_tool_use(self, tmp_path):
        f = tmp_path / "session.jsonl"
        entry = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
        _write_jsonl(f, [_user_entry(), entry])
        result = app._parse_jsonl_tail(f)
        assert result.get("tool") is None
        assert result["cwd"] == "/home/user/project"

    def test_returns_last_tool_when_multiple(self, tmp_path):
        f = tmp_path / "session.jsonl"
        _write_jsonl(f, [
            _assistant_entry("Read", ts="2026-01-01T10:00:00Z"),
            _assistant_entry("Write", ts="2026-01-01T10:00:05Z"),
        ])
        result = app._parse_jsonl_tail(f)
        assert result["tool"] == "Write"

    def test_handles_content_as_string(self, tmp_path):
        f = tmp_path / "session.jsonl"
        entry = {"type": "assistant", "message": {"content": "plain string"}}
        _write_jsonl(f, [entry])
        result = app._parse_jsonl_tail(f)
        assert result.get("tool") is None


# ── _detect_latest_thinking ────────────────────────────────────────────────────

class TestDetectLatestThinking:
    def test_returns_none_on_missing_file(self, tmp_path):
        result = app._detect_latest_thinking(tmp_path / "missing.jsonl")
        assert result is None

    def test_returns_none_when_no_thinking(self, tmp_path):
        f = tmp_path / "session.jsonl"
        _write_jsonl(f, [_assistant_entry("Read")])
        result = app._detect_latest_thinking(f)
        assert result is None

    def test_detects_thinking_block(self, tmp_path):
        f = tmp_path / "session.jsonl"
        _write_jsonl(f, [_assistant_entry("Read", thinking="I should read this file carefully")])
        result = app._detect_latest_thinking(f)
        assert result is not None
        assert result["text"] == "I should read this file carefully"
        assert "block_id" in result
        assert result["word_count"] == 6

    def test_returns_last_thinking_block(self, tmp_path):
        f = tmp_path / "session.jsonl"
        _write_jsonl(f, [
            _assistant_entry("Read", ts="2026-01-01T10:00:00Z", thinking="first thought"),
            _assistant_entry("Write", ts="2026-01-01T10:00:05Z", thinking="second thought"),
        ])
        result = app._detect_latest_thinking(f)
        assert result["text"] == "second thought"

    def test_block_id_is_deterministic(self, tmp_path):
        f = tmp_path / "session.jsonl"
        _write_jsonl(f, [_assistant_entry("Read", ts="2026-01-01T10:00:00Z", thinking="think")])
        r1 = app._detect_latest_thinking(f)
        r2 = app._detect_latest_thinking(f)
        assert r1["block_id"] == r2["block_id"]

    def test_ignores_empty_thinking(self, tmp_path):
        f = tmp_path / "session.jsonl"
        entry = {
            "type": "assistant",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {"content": [{"type": "thinking", "thinking": "   "}]},
        }
        _write_jsonl(f, [entry])
        result = app._detect_latest_thinking(f)
        assert result is None


# ── _get_project_stats ─────────────────────────────────────────────────────────

class TestGetProjectStats:
    def test_returns_empty_when_no_jsonl_dir(self, tmp_path):
        with patch.object(app, "CLAUDE_PROJECTS_DIR", tmp_path / "nonexistent"):
            result = app._get_project_stats(tmp_path / "proj", "proj")
        assert result == {}

    def test_returns_empty_when_no_jsonl_files(self, tmp_path):
        jsonl_dir = tmp_path / "encoded"
        jsonl_dir.mkdir()
        with patch.object(app, "CLAUDE_PROJECTS_DIR", tmp_path):
            with patch.object(app, "_get_jsonl_dir", return_value=jsonl_dir):
                # patch Path.home() usage inside _get_project_stats
                with patch("app.Path.home", return_value=tmp_path):
                    result = app._get_project_stats(tmp_path / "proj", "proj")
        assert result == {}

    def test_parses_token_stats(self, tmp_path):
        project_path = tmp_path / "proj"
        encoded = str(project_path).replace("/", "-")
        jsonl_dir = tmp_path / ".claude" / "projects" / encoded
        jsonl_dir.mkdir(parents=True)
        f = jsonl_dir / "session.jsonl"
        _write_jsonl(f, [
            _assistant_entry("Read", input_tokens=500, output_tokens=200, model="claude-sonnet-4-6"),
            _assistant_entry("Write", input_tokens=300, output_tokens=100, model="claude-sonnet-4-6"),
        ])
        app._jsonl_mtimes.clear()
        app._project_stats_cache.clear()

        with patch("app.Path.home", return_value=tmp_path):
            result = app._get_project_stats(project_path, "test-proj-stats")

        assert result["session_input_tokens"] == 800
        assert result["session_output_tokens"] == 300
        assert result["model"] == "claude-sonnet-4-6"
        assert result["session_ctx_tokens"] > 0

    def test_uses_cache_when_mtime_unchanged(self, tmp_path):
        project_path = tmp_path / "proj"
        encoded = str(project_path).replace("/", "-")
        jsonl_dir = tmp_path / ".claude" / "projects" / encoded
        jsonl_dir.mkdir(parents=True)
        f = jsonl_dir / "session.jsonl"
        _write_jsonl(f, [_assistant_entry("Read", input_tokens=100)])
        app._jsonl_mtimes.clear()
        app._project_stats_cache.clear()

        with patch("app.Path.home", return_value=tmp_path):
            r1 = app._get_project_stats(project_path, "cached-proj-x")
            r2 = app._get_project_stats(project_path, "cached-proj-x")

        assert r1 == r2


# ── _parse_session_detail ──────────────────────────────────────────────────────

class TestParseSessionDetail:
    def test_returns_empty_on_oserror(self, tmp_path):
        result = app._parse_session_detail(tmp_path / "missing.jsonl")
        assert result["thinking"] == []
        assert result["tools"] == []

    def test_parses_tool_calls(self, tmp_path):
        f = tmp_path / "session.jsonl"
        ts_call = "2026-01-01T10:00:00Z"
        ts_result = "2026-01-01T10:00:01Z"
        entries = [
            {
                "type": "assistant",
                "timestamp": ts_call,
                "message": {
                    "content": [{"type": "tool_use", "id": "tu_1", "name": "Read", "input": {"file_path": "/foo.py"}}],
                    "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0},
                    "model": "claude-sonnet-4-6",
                },
            },
            {
                "type": "user",
                "timestamp": ts_result,
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "tu_1", "is_error": False}]
                },
            },
        ]
        _write_jsonl(f, entries)
        result = app._parse_session_detail(f)
        assert len(result["tools"]) == 1
        assert result["tools"][0]["tool"] == "Read"
        assert result["tools"][0]["success"] is True
        assert result["tools"][0]["duration_ms"] == 1000

    def test_parses_thinking_blocks(self, tmp_path):
        f = tmp_path / "session.jsonl"
        _write_jsonl(f, [_assistant_entry("Read", thinking="deep thought here")])
        result = app._parse_session_detail(f)
        assert len(result["thinking"]) == 1
        assert result["thinking"][0]["text"] == "deep thought here"

    def test_accumulates_token_stats(self, tmp_path):
        f = tmp_path / "session.jsonl"
        _write_jsonl(f, [
            _assistant_entry("Read", input_tokens=100, output_tokens=50),
            _assistant_entry("Write", input_tokens=200, output_tokens=80),
        ])
        result = app._parse_session_detail(f)
        assert result["stats"]["input_tokens"] == 300
        assert result["stats"]["output_tokens"] == 130

    def test_marks_tool_as_error(self, tmp_path):
        f = tmp_path / "session.jsonl"
        entries = [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "content": [{"type": "tool_use", "id": "tu_2", "name": "Bash", "input": {}}],
                    "usage": {"input_tokens": 5, "output_tokens": 2, "cache_read_input_tokens": 0},
                },
            },
            {
                "type": "user",
                "timestamp": "2026-01-01T10:00:02Z",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "tu_2", "is_error": True}]
                },
            },
        ]
        _write_jsonl(f, entries)
        result = app._parse_session_detail(f)
        assert result["tools"][0]["success"] is False


# ── _list_sessions ─────────────────────────────────────────────────────────────

class TestListSessions:
    def test_returns_empty_when_project_not_tracked(self):
        original = dict(app._status_paths)
        app._status_paths.clear()
        try:
            result = app._list_sessions("unknown-project")
            assert result == []
        finally:
            app._status_paths.update(original)

    def test_returns_empty_when_jsonl_dir_missing(self, tmp_path):
        fake_status = tmp_path / "proj" / ".claude" / "status.json"
        fake_status.parent.mkdir(parents=True)
        fake_status.write_text("{}")
        app._status_paths["test-ls-proj"] = fake_status
        try:
            with patch.object(app, "CLAUDE_PROJECTS_DIR", tmp_path / "nonexistent"):
                result = app._list_sessions("test-ls-proj")
            assert result == []
        finally:
            del app._status_paths["test-ls-proj"]

    def test_lists_sessions_sorted_newest_first(self, tmp_path):
        project_dir = tmp_path / "proj"
        fake_status = project_dir / ".claude" / "status.json"
        fake_status.parent.mkdir(parents=True)
        fake_status.write_text("{}")

        encoded = str(project_dir).replace("/", "-")
        jsonl_dir = tmp_path / "claude_projects" / encoded
        jsonl_dir.mkdir(parents=True)

        old_f = jsonl_dir / "old_session.jsonl"
        new_f = jsonl_dir / "new_session.jsonl"
        _write_jsonl(old_f, [{"type": "user", "timestamp": "2026-01-01T09:00:00Z"}])
        time.sleep(0.02)
        _write_jsonl(new_f, [{"type": "user", "timestamp": "2026-01-01T10:00:00Z"}])

        app._status_paths["test-ls-proj2"] = fake_status
        try:
            with patch.object(app, "CLAUDE_PROJECTS_DIR", tmp_path / "claude_projects"):
                result = app._list_sessions("test-ls-proj2")
            assert len(result) == 2
            assert result[0]["session_id"] == "new_session"
            assert result[1]["session_id"] == "old_session"
            assert "_mtime" not in result[0]
        finally:
            del app._status_paths["test-ls-proj2"]

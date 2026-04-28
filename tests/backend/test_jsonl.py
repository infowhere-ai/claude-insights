"""Tests for JSONL parsing functions in app.py.

These tests exercise: _parse_jsonl_tail, _parse_session_detail,
_detect_latest_thinking, and _get_project_stats with fixture files.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import app


# ── _parse_jsonl_tail ─────────────────────────────────────────────────────────

class TestParseJsonlTail:
    def test_extracts_last_tool_use(self, sample_jsonl):
        result = app._parse_jsonl_tail(sample_jsonl)
        assert result["tool"] == "Read"

    def test_extracts_cwd_when_present(self, tmp_jsonl_dir):
        session = tmp_jsonl_dir / "cwd_test.jsonl"
        lines = [
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-01-01T12:00:00Z",
                "cwd": "/home/user/project",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 1, "output_tokens": 1,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                    "content": [],
                },
            }),
        ]
        session.write_text("\n".join(lines), encoding="utf-8")
        result = app._parse_jsonl_tail(session)
        assert result["cwd"] == "/home/user/project"

    def test_returns_empty_dict_for_nonexistent_file(self, tmp_path):
        result = app._parse_jsonl_tail(tmp_path / "nonexistent.jsonl")
        assert result == {}

    def test_returns_empty_dict_for_corrupt_content(self, tmp_jsonl_dir):
        session = tmp_jsonl_dir / "corrupt.jsonl"
        session.write_text("not json at all\n{broken", encoding="utf-8")
        result = app._parse_jsonl_tail(session)
        assert isinstance(result, dict)

    def test_tool_is_none_when_no_tool_use(self, tmp_jsonl_dir):
        session = tmp_jsonl_dir / "no_tool.jsonl"
        lines = [
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-01-01T13:00:00Z",
                "message": {
                    "content": [{"type": "text", "text": "Hello!"}],
                    "usage": {"input_tokens": 1, "output_tokens": 1,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            }),
        ]
        session.write_text("\n".join(lines), encoding="utf-8")
        result = app._parse_jsonl_tail(session)
        assert result.get("tool") is None


# ── _detect_latest_thinking ───────────────────────────────────────────────────

class TestDetectLatestThinking:
    def test_detects_thinking_block(self, sample_thinking_jsonl):
        result = app._detect_latest_thinking(sample_thinking_jsonl)
        assert result is not None
        assert "carefully analyse" in result["text"]
        assert "block_id" in result
        assert "word_count" in result
        assert result["word_count"] > 0

    def test_returns_none_when_no_thinking(self, sample_jsonl):
        result = app._detect_latest_thinking(sample_jsonl)
        assert result is None

    def test_returns_none_for_empty_file(self, tmp_jsonl_dir):
        empty = tmp_jsonl_dir / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        result = app._detect_latest_thinking(empty)
        assert result is None

    def test_block_id_is_stable_for_same_timestamp(self, tmp_jsonl_dir):
        """Same timestamp → same block_id (SSE deduplication relies on this)."""
        line = json.dumps({
            "type": "assistant",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {
                "content": [{"type": "thinking", "thinking": "Some thought."}],
                "usage": {"input_tokens": 1, "output_tokens": 1,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            },
        })
        session = tmp_jsonl_dir / "stable.jsonl"
        session.write_text(line + "\n", encoding="utf-8")
        r1 = app._detect_latest_thinking(session)
        r2 = app._detect_latest_thinking(session)
        assert r1["block_id"] == r2["block_id"]

    def test_empty_thinking_block_is_ignored(self, tmp_jsonl_dir):
        line = json.dumps({
            "type": "assistant",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {
                "content": [{"type": "thinking", "thinking": "   "}],
                "usage": {"input_tokens": 1, "output_tokens": 1,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            },
        })
        session = tmp_jsonl_dir / "empty_think.jsonl"
        session.write_text(line + "\n", encoding="utf-8")
        result = app._detect_latest_thinking(session)
        assert result is None


# ── _parse_session_detail ─────────────────────────────────────────────────────

class TestParseSessionDetail:
    def test_extracts_tool_event(self, sample_jsonl):
        result = app._parse_session_detail(sample_jsonl)
        assert "tools" in result
        assert len(result["tools"]) == 1
        tool = result["tools"][0]
        assert tool["tool"] == "Read"
        assert tool["success"] is True

    def test_extracts_stats(self, sample_jsonl):
        result = app._parse_session_detail(sample_jsonl)
        stats = result["stats"]
        assert stats["input_tokens"] == 100
        assert stats["output_tokens"] == 50
        assert stats["cache_read_tokens"] == 200
        assert stats["model"] == "claude-sonnet-4-6"

    def test_extracts_thinking(self, sample_thinking_jsonl):
        result = app._parse_session_detail(sample_thinking_jsonl)
        assert len(result["thinking"]) == 1
        assert "carefully analyse" in result["thinking"][0]["text"]

    def test_returns_empty_for_nonexistent_file(self, tmp_path):
        result = app._parse_session_detail(tmp_path / "none.jsonl")
        assert result["tools"] == []
        assert result["thinking"] == []

    def test_duration_ms_calculated(self, tmp_jsonl_dir):
        """Tool duration = time between tool_use (assistant) and tool_result (user)."""
        session = tmp_jsonl_dir / "duration.jsonl"
        lines = [
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 1, "output_tokens": 1,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                    "content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                                  "input": {"command": "ls"}}],
                },
            }),
            json.dumps({
                "type": "user",
                "timestamp": "2026-01-01T10:00:02Z",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "t1",
                                  "content": "file.py", "is_error": False}]
                },
            }),
        ]
        session.write_text("\n".join(lines), encoding="utf-8")
        result = app._parse_session_detail(session)
        assert result["tools"][0]["duration_ms"] == 2000

    def test_is_error_flag_propagated(self, tmp_jsonl_dir):
        session = tmp_jsonl_dir / "error.jsonl"
        lines = [
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 1, "output_tokens": 1,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                    "content": [{"type": "tool_use", "id": "t2", "name": "Bash",
                                  "input": {"command": "bad command"}}],
                },
            }),
            json.dumps({
                "type": "user",
                "timestamp": "2026-01-01T10:00:01Z",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "t2",
                                  "content": "error output", "is_error": True}]
                },
            }),
        ]
        session.write_text("\n".join(lines), encoding="utf-8")
        result = app._parse_session_detail(session)
        assert result["tools"][0]["success"] is False

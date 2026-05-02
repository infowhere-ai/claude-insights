"""Unit tests for JSONL parsing functions."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from claude_monitor.jsonl import parser


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


# ── tool_input_summary ────────────────────────────────────────────────────────

class TestToolInputSummary:
    def test_read_returns_file_path(self):
        assert parser.tool_input_summary("Read", {"file_path": "/src/main.py"}) == "/src/main.py"

    def test_write_returns_file_path(self):
        assert parser.tool_input_summary("Write", {"file_path": "/src/new.py"}) == "/src/new.py"

    def test_edit_returns_file_path(self):
        assert parser.tool_input_summary("Edit", {"file_path": "/src/edit.py"}) == "/src/edit.py"

    def test_bash_returns_command_truncated(self):
        long_cmd = "x" * 100
        result = parser.tool_input_summary("Bash", {"command": long_cmd})
        assert result == long_cmd[:80]

    def test_bash_returns_command_short(self):
        assert parser.tool_input_summary("Bash", {"command": "ls -la"}) == "ls -la"

    def test_glob_returns_pattern(self):
        assert parser.tool_input_summary("Glob", {"pattern": "**/*.py"}) == "**/*.py"

    def test_grep_returns_pattern(self):
        assert parser.tool_input_summary("Grep", {"pattern": "def test_"}) == "def test_"

    def test_webfetch_returns_url(self):
        assert parser.tool_input_summary("WebFetch", {"url": "https://example.com"}) == "https://example.com"

    def test_websearch_returns_query(self):
        assert parser.tool_input_summary("WebSearch", {"query": "python asyncio"}) == "python asyncio"

    def test_generic_returns_first_string_value(self):
        result = parser.tool_input_summary("Agent", {"description": "do something"})
        assert result == "do something"

    def test_generic_returns_empty_for_no_strings(self):
        result = parser.tool_input_summary("Unknown", {"count": 42})
        assert result == ""

    def test_read_falls_back_to_path(self):
        assert parser.tool_input_summary("Read", {"path": "/alt/path.py"}) == "/alt/path.py"


# ── tool_detail ───────────────────────────────────────────────────────────────

class TestToolDetail:
    def test_bash_type(self):
        detail = parser.tool_detail("Bash", {"command": "git status", "description": "Show git status"})
        assert detail["type"] == "bash"
        assert detail["command"] == "git status"
        assert detail["description"] == "Show git status"

    def test_bash_missing_description_is_empty(self):
        detail = parser.tool_detail("Bash", {"command": "ls"})
        assert detail["description"] == ""

    def test_edit_type_with_diff(self):
        detail = parser.tool_detail("Edit", {
            "file_path": "app.py",
            "old_string": "foo = 1\n",
            "new_string": "foo = 2\n",
        })
        assert detail["type"] == "edit"
        assert detail["file_path"] == "app.py"
        assert "-foo = 1" in detail["diff"]
        assert "+foo = 2" in detail["diff"]

    def test_edit_empty_strings_no_diff(self):
        detail = parser.tool_detail("Edit", {"file_path": "a.py", "old_string": "", "new_string": ""})
        assert detail["type"] == "edit"
        assert detail["diff"] == ""

    def test_write_type(self):
        detail = parser.tool_detail("Write", {"file_path": "out.py", "content": "print('hi')"})
        assert detail["type"] == "write"
        assert detail["file_path"] == "out.py"
        assert detail["content"] == "print('hi')"
        assert detail["total_chars"] == len("print('hi')")

    def test_write_content_truncated_at_3000(self):
        content = "x" * 5000
        detail = parser.tool_detail("Write", {"file_path": "big.py", "content": content})
        assert len(detail["content"]) == 3000
        assert detail["total_chars"] == 5000

    def test_read_type(self):
        detail = parser.tool_detail("Read", {"file_path": "src.py", "limit": 100, "offset": 50})
        assert detail["type"] == "read"
        assert detail["file_path"] == "src.py"
        assert detail["limit"] == 100
        assert detail["offset"] == 50

    def test_grep_type(self):
        detail = parser.tool_detail("Grep", {"pattern": "TODO", "path": "/src"})
        assert detail["type"] == "search"
        assert detail["tool"] == "Grep"

    def test_glob_type(self):
        detail = parser.tool_detail("Glob", {"pattern": "*.py", "path": "/src"})
        assert detail["type"] == "search"
        assert detail["tool"] == "Glob"

    def test_webfetch_type(self):
        detail = parser.tool_detail("WebFetch", {"url": "https://example.com"})
        assert detail["type"] == "web"

    def test_websearch_type(self):
        detail = parser.tool_detail("WebSearch", {"query": "fastapi docs"})
        assert detail["type"] == "web"

    def test_agent_type(self):
        detail = parser.tool_detail("Agent", {"description": "Run subagent", "prompt": "Do the thing"})
        assert detail["type"] == "agent"
        assert "Do the thing" in detail["prompt"]

    def test_generic_type_for_unknown_tool(self):
        detail = parser.tool_detail("CustomTool", {"key": "value"})
        assert detail["type"] == "generic"
        assert "key" in detail["fields"]


# ── get_jsonl_dir ─────────────────────────────────────────────────────────────

class TestGetJsonlDir:
    def test_encodes_slashes_as_dashes(self):
        project_path = Path("/home/user/my-project")
        result = parser.get_jsonl_dir(project_path)
        assert result.name == "-home-user-my-project"

    def test_result_is_under_claude_projects(self):
        project_path = Path("/home/user/project")
        result = parser.get_jsonl_dir(project_path)
        assert result.parent == Path.home() / ".claude" / "projects"


# ── parse_jsonl_tail ──────────────────────────────────────────────────────────

class TestParseJsonlTail:
    def test_extracts_last_tool_use(self, sample_jsonl):
        result = parser.parse_jsonl_tail(sample_jsonl)
        assert result.get("tool") == "Read"

    def test_extracts_cwd_when_present(self, tmp_jsonl_dir):
        session = tmp_jsonl_dir / "cwd_test.jsonl"
        _write_jsonl(session, [_user_entry(cwd="/home/user/project")])
        result = parser.parse_jsonl_tail(session)
        assert result.get("cwd") == "/home/user/project"

    def test_returns_empty_dict_for_nonexistent_file(self, tmp_path):
        result = parser.parse_jsonl_tail(tmp_path / "nonexistent.jsonl")
        assert result == {}

    def test_returns_empty_dict_for_corrupt_content(self, tmp_jsonl_dir):
        session = tmp_jsonl_dir / "corrupt.jsonl"
        session.write_text("not json\nalso not json\n", encoding="utf-8")
        result = parser.parse_jsonl_tail(session)
        assert result.get("tool") is None

    def test_tool_is_none_when_no_tool_use(self, tmp_jsonl_dir):
        session = tmp_jsonl_dir / "no_tool.jsonl"
        _write_jsonl(session, [{"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}])
        result = parser.parse_jsonl_tail(session)
        assert result.get("tool") is None


# ── detect_latest_thinking ────────────────────────────────────────────────────

class TestDetectLatestThinking:
    def test_detects_thinking_block(self, sample_thinking_jsonl):
        result = parser.detect_latest_thinking(sample_thinking_jsonl)
        assert result is not None
        assert "analyse" in result["text"]
        assert "block_id" in result

    def test_returns_none_when_no_thinking(self, sample_jsonl):
        result = parser.detect_latest_thinking(sample_jsonl)
        assert result is None

    def test_returns_none_for_empty_file(self, tmp_jsonl_dir):
        empty = tmp_jsonl_dir / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        result = parser.detect_latest_thinking(empty)
        assert result is None

    def test_block_id_is_stable_for_same_timestamp(self, tmp_jsonl_dir):
        session = tmp_jsonl_dir / "stable.jsonl"
        entry = _assistant_entry(thinking="Deep thought here", ts="2026-01-01T10:00:00Z")
        _write_jsonl(session, [entry])
        r1 = parser.detect_latest_thinking(session)
        r2 = parser.detect_latest_thinking(session)
        assert r1 is not None and r2 is not None
        assert r1["block_id"] == r2["block_id"]

    def test_empty_thinking_block_is_ignored(self, tmp_jsonl_dir):
        session = tmp_jsonl_dir / "empty_thinking.jsonl"
        entry = {
            "type": "assistant",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {"content": [{"type": "thinking", "thinking": "   "}]},
        }
        _write_jsonl(session, [entry])
        assert parser.detect_latest_thinking(session) is None


# ── parse_session_detail ──────────────────────────────────────────────────────

class TestParseSessionDetail:
    def test_extracts_tool_event(self, sample_jsonl):
        result = parser.parse_session_detail(sample_jsonl)
        assert len(result["tools"]) == 1
        assert result["tools"][0]["tool"] == "Read"

    def test_extracts_stats(self, sample_jsonl):
        result = parser.parse_session_detail(sample_jsonl)
        assert result["stats"]["input_tokens"] == 100
        assert result["stats"]["output_tokens"] == 50

    def test_extracts_thinking(self, sample_thinking_jsonl):
        result = parser.parse_session_detail(sample_thinking_jsonl)
        assert len(result["thinking"]) == 1
        assert "analyse" in result["thinking"][0]["text"]

    def test_returns_empty_for_nonexistent_file(self, tmp_path):
        result = parser.parse_session_detail(tmp_path / "ghost.jsonl")
        assert result["thinking"] == []
        assert result["tools"] == []

    def test_duration_ms_calculated(self, tmp_jsonl_dir):
        session = tmp_jsonl_dir / "duration.jsonl"
        _write_jsonl(session, [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                    "usage": {"input_tokens": 1, "output_tokens": 1,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            },
            {
                "type": "user",
                "timestamp": "2026-01-01T10:00:02Z",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
            },
        ])
        result = parser.parse_session_detail(session)
        assert result["tools"][0]["duration_ms"] == 2000

    def test_is_error_flag_propagated(self, tmp_jsonl_dir):
        session = tmp_jsonl_dir / "error.jsonl"
        _write_jsonl(session, [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {}}],
                    "usage": {"input_tokens": 1, "output_tokens": 1,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                },
            },
            {
                "type": "user",
                "timestamp": "2026-01-01T10:00:01Z",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "t2",
                                         "content": "err", "is_error": True}]},
            },
        ])
        result = parser.parse_session_detail(session)
        assert result["tools"][0]["success"] is False

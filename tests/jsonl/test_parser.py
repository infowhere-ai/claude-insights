"""Unit tests for JSONL parsing functions."""

import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from claude_monitor.jsonl import parser


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


def _assistant_entry(
    tool_name: str = "Read",
    ts: str = "2026-01-01T10:00:00Z",
    input_tokens: int = 100,
    output_tokens: int = 50,
    model: str = "claude-sonnet-4-6",
    thinking: str = "",
) -> dict:
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


# ── _read_jsonl_entries ───────────────────────────────────────────────────────


class TestReadJsonlEntries:
    def test_returns_parsed_entries(self, tmp_jsonl_dir):
        f = tmp_jsonl_dir / "entries.jsonl"
        f.write_text(
            '{"type": "assistant"}\n{"type": "user"}\n',
            encoding="utf-8",
        )
        result = parser._read_jsonl_entries(f)
        assert len(result) == 2
        assert result[0]["type"] == "assistant"
        assert result[1]["type"] == "user"

    def test_skips_invalid_lines(self, tmp_jsonl_dir):
        f = tmp_jsonl_dir / "bad.jsonl"
        f.write_text('not json\n{"type": "user"}\n', encoding="utf-8")
        result = parser._read_jsonl_entries(f)
        assert len(result) == 1

    def test_returns_empty_list_on_missing_file(self, tmp_path):
        result = parser._read_jsonl_entries(tmp_path / "ghost.jsonl")
        assert result == []

    def test_skips_blank_lines(self, tmp_jsonl_dir):
        f = tmp_jsonl_dir / "blanks.jsonl"
        f.write_text('\n{"type": "assistant"}\n\n', encoding="utf-8")
        result = parser._read_jsonl_entries(f)
        assert len(result) == 1


# ── _collect_tool_results ─────────────────────────────────────────────────────


class TestCollectToolResults:
    def test_collects_tool_result_from_user_entry(self):
        entries = [
            {
                "type": "user",
                "timestamp": "2026-01-01T10:00:01Z",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tid_abc",
                            "is_error": False,
                        }
                    ]
                },
            }
        ]
        result = parser._collect_tool_results(entries)
        assert "tid_abc" in result
        assert result["tid_abc"]["timestamp"] == "2026-01-01T10:00:01Z"
        assert result["tid_abc"]["is_error"] is False

    def test_marks_error_flag(self):
        entries = [
            {
                "type": "user",
                "timestamp": "2026-01-01T10:00:01Z",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tid_err",
                            "is_error": True,
                        }
                    ]
                },
            }
        ]
        result = parser._collect_tool_results(entries)
        assert result["tid_err"]["is_error"] is True

    def test_ignores_non_user_entries(self):
        entries = [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "should_be_ignored"}]
                },
            }
        ]
        result = parser._collect_tool_results(entries)
        assert result == {}

    def test_ignores_non_tool_result_content(self):
        entries = [
            {
                "type": "user",
                "timestamp": "2026-01-01T10:00:01Z",
                "message": {"content": [{"type": "text", "text": "hello"}]},
            }
        ]
        result = parser._collect_tool_results(entries)
        assert result == {}

    def test_returns_empty_dict_for_no_entries(self):
        assert parser._collect_tool_results([]) == {}


# ── _calculate_duration_ms ────────────────────────────────────────────────────


class TestCalculateDurationMs:
    def test_returns_duration_in_milliseconds(self):
        result = parser._calculate_duration_ms("2026-01-01T10:00:00Z", "2026-01-01T10:00:02Z")
        assert result == 2000

    def test_returns_none_for_empty_ts(self):
        assert parser._calculate_duration_ms("", "2026-01-01T10:00:02Z") is None

    def test_returns_none_for_empty_rts(self):
        assert parser._calculate_duration_ms("2026-01-01T10:00:00Z", "") is None

    def test_returns_none_for_invalid_iso_string(self):
        assert parser._calculate_duration_ms("not-a-date", "2026-01-01T10:00:02Z") is None

    def test_fractional_seconds(self):
        result = parser._calculate_duration_ms("2026-01-01T10:00:00Z", "2026-01-01T10:00:00.500Z")
        assert result == 500


# ── _process_assistant_entry ──────────────────────────────────────────────────


class TestProcessAssistantEntry:
    def _make_entry(self, ts="2026-01-01T10:00:00Z", thinking="", tool_id="t1"):
        content: list[dict] = []
        if thinking:
            content.append({"type": "thinking", "thinking": thinking})
        content.append({"type": "tool_use", "id": tool_id, "name": "Read", "input": {}})
        return {
            "type": "assistant",
            "timestamp": ts,
            "message": {
                "model": "claude-sonnet-4-6",
                "content": content,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 20,
                },
            },
        }

    def test_updates_stats_tokens(self):
        entry = self._make_entry()
        thinking: list = []
        tools: list = []
        stats = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "model": ""}
        parser._process_assistant_entry(entry, {}, thinking, tools, stats)
        assert stats["input_tokens"] == 100
        assert stats["output_tokens"] == 50
        assert stats["cache_read_tokens"] == 20

    def test_appends_tool(self):
        entry = self._make_entry()
        thinking: list = []
        tools: list = []
        stats = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "model": ""}
        parser._process_assistant_entry(entry, {}, thinking, tools, stats)
        assert len(tools) == 1
        assert tools[0]["tool"] == "Read"

    def test_appends_thinking_block(self):
        entry = self._make_entry(thinking="deep thoughts here")
        thinking: list = []
        tools: list = []
        stats = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "model": ""}
        parser._process_assistant_entry(entry, {}, thinking, tools, stats)
        assert len(thinking) == 1
        assert "deep thoughts" in thinking[0]["text"]

    def test_skips_empty_thinking_block(self):
        entry = self._make_entry(thinking="   ")
        thinking: list = []
        tools: list = []
        stats = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "model": ""}
        parser._process_assistant_entry(entry, {}, thinking, tools, stats)
        assert len(thinking) == 0

    def test_sets_model(self):
        entry = self._make_entry()
        thinking: list = []
        tools: list = []
        stats = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "model": ""}
        parser._process_assistant_entry(entry, {}, thinking, tools, stats)
        assert stats["model"] == "claude-sonnet-4-6"

    def test_tool_success_from_tool_results(self):
        entry = self._make_entry(tool_id="tid1")
        tool_results = {"tid1": {"timestamp": "2026-01-01T10:00:01Z", "is_error": False}}
        thinking: list = []
        tools: list = []
        stats = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "model": ""}
        parser._process_assistant_entry(entry, tool_results, thinking, tools, stats)
        assert tools[0]["success"] is True

    def test_tool_failure_from_tool_results(self):
        entry = self._make_entry(tool_id="tid2")
        tool_results = {"tid2": {"timestamp": "2026-01-01T10:00:01Z", "is_error": True}}
        thinking: list = []
        tools: list = []
        stats = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "model": ""}
        parser._process_assistant_entry(entry, tool_results, thinking, tools, stats)
        assert tools[0]["success"] is False

    def test_ignores_non_list_content(self):
        entry = {
            "type": "assistant",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {
                "model": "claude-sonnet-4-6",
                "content": "plain string",
                "usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0},
            },
        }
        thinking: list = []
        tools: list = []
        stats = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "model": ""}
        parser._process_assistant_entry(entry, {}, thinking, tools, stats)
        assert len(tools) == 0


# ── _read_tail_bytes ──────────────────────────────────────────────────────────


class TestReadTailBytes:
    def test_returns_string_content(self, tmp_jsonl_dir):
        f = tmp_jsonl_dir / "tail.jsonl"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")
        result = parser._read_tail_bytes(f, 8192)
        assert "line1" in result
        assert "line3" in result

    def test_returns_last_bytes_only(self, tmp_jsonl_dir):
        f = tmp_jsonl_dir / "big.jsonl"
        content = "a" * 100 + "LAST"
        f.write_bytes(content.encode("utf-8"))
        result = parser._read_tail_bytes(f, 10)
        assert "LAST" in result
        assert result.startswith("a") is False or len(result) <= 12

    def test_returns_empty_string_on_error(self, tmp_path):
        result = parser._read_tail_bytes(tmp_path / "nonexistent.jsonl", 8192)
        assert result == ""


# ── _extract_tool_from_content ────────────────────────────────────────────────


class TestExtractToolFromContent:
    def test_returns_tool_name(self):
        content = [{"type": "tool_use", "name": "Bash", "id": "t1", "input": {}}]
        assert parser._extract_tool_from_content(content) == "Bash"

    def test_returns_none_when_no_tool_use(self):
        content = [{"type": "text", "text": "hello"}]
        assert parser._extract_tool_from_content(content) is None

    def test_returns_none_for_empty_list(self):
        assert parser._extract_tool_from_content([]) is None

    def test_returns_last_tool_use_in_list(self):
        content = [
            {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
            {"type": "tool_use", "name": "Write", "id": "t2", "input": {}},
        ]
        # reversed iteration returns last from original list (Write in reversed = first found)
        result = parser._extract_tool_from_content(content)
        assert result == "Write"

    def test_returns_tool_name_default_when_empty_name(self):
        content = [{"type": "tool_use", "name": "", "id": "t1", "input": {}}]
        result = parser._extract_tool_from_content(content)
        assert result == "Tool"


# ── _extract_thinking_block ───────────────────────────────────────────────────


class TestExtractThinkingBlock:
    def test_returns_thinking_block(self):
        entry = {
            "type": "assistant",
            "timestamp": "2026-01-01T11:00:00Z",
            "message": {"content": [{"type": "thinking", "thinking": "some deep thought"}]},
        }
        result = parser._extract_thinking_block(entry)
        assert result is not None
        assert result["text"] == "some deep thought"
        assert "block_id" in result

    def test_returns_none_for_empty_thinking(self):
        entry = {
            "type": "assistant",
            "timestamp": "2026-01-01T11:00:00Z",
            "message": {"content": [{"type": "thinking", "thinking": "  "}]},
        }
        assert parser._extract_thinking_block(entry) is None

    def test_returns_none_when_no_thinking_in_content(self):
        entry = {
            "type": "assistant",
            "timestamp": "2026-01-01T11:00:00Z",
            "message": {"content": [{"type": "text", "text": "answer"}]},
        }
        assert parser._extract_thinking_block(entry) is None

    def test_returns_none_for_non_assistant_entry(self):
        entry = {
            "type": "user",
            "timestamp": "2026-01-01T11:00:00Z",
            "message": {"content": [{"type": "thinking", "thinking": "thoughts"}]},
        }
        assert parser._extract_thinking_block(entry) is None

    def test_block_id_stable_for_same_timestamp(self):
        entry = {
            "type": "assistant",
            "timestamp": "2026-01-01T11:00:00Z",
            "message": {"content": [{"type": "thinking", "thinking": "stable thought"}]},
        }
        r1 = parser._extract_thinking_block(entry)
        r2 = parser._extract_thinking_block(entry)
        assert r1 is not None and r2 is not None
        assert r1["block_id"] == r2["block_id"]


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
        assert (
            parser.tool_input_summary("WebFetch", {"url": "https://example.com"})
            == "https://example.com"
        )

    def test_websearch_returns_query(self):
        assert (
            parser.tool_input_summary("WebSearch", {"query": "python asyncio"}) == "python asyncio"
        )

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
        detail = parser.tool_detail(
            "Bash", {"command": "git status", "description": "Show git status"}
        )
        assert detail["type"] == "bash"
        assert detail["command"] == "git status"
        assert detail["description"] == "Show git status"

    def test_bash_missing_description_is_empty(self):
        detail = parser.tool_detail("Bash", {"command": "ls"})
        assert detail["description"] == ""

    def test_edit_type_with_diff(self):
        detail = parser.tool_detail(
            "Edit",
            {
                "file_path": "app.py",
                "old_string": "foo = 1\n",
                "new_string": "foo = 2\n",
            },
        )
        assert detail["type"] == "edit"
        assert detail["file_path"] == "app.py"
        assert "-foo = 1" in detail["diff"]
        assert "+foo = 2" in detail["diff"]

    def test_edit_empty_strings_no_diff(self):
        detail = parser.tool_detail(
            "Edit", {"file_path": "a.py", "old_string": "", "new_string": ""}
        )
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
        detail = parser.tool_detail(
            "Agent", {"description": "Run subagent", "prompt": "Do the thing"}
        )
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


# ── _parse_jsonl_tail_line ────────────────────────────────────────────────────


class TestParseJsonlTailLine:
    """Tests for _parse_jsonl_tail_line helper (extracted from parse_jsonl_tail)."""

    def test_extracts_cwd_from_user_entry(self):
        """
        Given a user entry with a cwd field
        When _parse_jsonl_tail_line is called with cwd=None, tool=None
        Then it returns (tool=None, cwd='/some/path')
        """
        from claude_monitor.jsonl.parser import _parse_jsonl_tail_line

        d = {"type": "user", "cwd": "/some/path", "message": {"content": []}}
        tool, cwd = _parse_jsonl_tail_line(d, current_cwd=None, current_tool=None)
        assert cwd == "/some/path"
        assert tool is None

    def test_extracts_tool_from_assistant_entry(self):
        """
        Given an assistant entry with a tool_use block
        When _parse_jsonl_tail_line is called with tool=None
        Then it returns the tool name
        """
        from claude_monitor.jsonl.parser import _parse_jsonl_tail_line

        d = {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Bash", "id": "t1", "input": {}}]},
        }
        tool, cwd = _parse_jsonl_tail_line(d, current_cwd=None, current_tool=None)
        assert tool == "Bash"
        assert cwd is None

    def test_does_not_overwrite_existing_cwd(self):
        """
        Given a user entry with cwd and current_cwd already set
        When _parse_jsonl_tail_line is called
        Then the existing cwd is preserved
        """
        from claude_monitor.jsonl.parser import _parse_jsonl_tail_line

        d = {"type": "user", "cwd": "/new/path", "message": {"content": []}}
        tool, cwd = _parse_jsonl_tail_line(d, current_cwd="/old/path", current_tool=None)
        assert cwd == "/old/path"

    def test_does_not_overwrite_existing_tool(self):
        """
        Given an assistant entry and current_tool already set
        When _parse_jsonl_tail_line is called
        Then the existing tool is preserved
        """
        from claude_monitor.jsonl.parser import _parse_jsonl_tail_line

        d = {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Read", "id": "t1", "input": {}}]},
        }
        tool, cwd = _parse_jsonl_tail_line(d, current_cwd=None, current_tool="Bash")
        assert tool == "Bash"

    def test_returns_none_for_unknown_type(self):
        """
        Given an entry that is neither user nor assistant
        When _parse_jsonl_tail_line is called
        Then nothing changes
        """
        from claude_monitor.jsonl.parser import _parse_jsonl_tail_line

        d = {"type": "system", "message": {}}
        tool, cwd = _parse_jsonl_tail_line(d, current_cwd=None, current_tool=None)
        assert tool is None
        assert cwd is None


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
        _write_jsonl(
            session,
            [{"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}],
        )
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
        _write_jsonl(
            session,
            [
                {
                    "type": "assistant",
                    "timestamp": "2026-01-01T10:00:00Z",
                    "message": {
                        "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                        "usage": {
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    },
                },
                {
                    "type": "user",
                    "timestamp": "2026-01-01T10:00:02Z",
                    "message": {
                        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]
                    },
                },
            ],
        )
        result = parser.parse_session_detail(session)
        assert result["tools"][0]["duration_ms"] == 2000

    def test_is_error_flag_propagated(self, tmp_jsonl_dir):
        session = tmp_jsonl_dir / "error.jsonl"
        _write_jsonl(
            session,
            [
                {
                    "type": "assistant",
                    "timestamp": "2026-01-01T10:00:00Z",
                    "message": {
                        "content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {}}],
                        "usage": {
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    },
                },
                {
                    "type": "user",
                    "timestamp": "2026-01-01T10:00:01Z",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "t2",
                                "content": "err",
                                "is_error": True,
                            }
                        ]
                    },
                },
            ],
        )
        result = parser.parse_session_detail(session)
        assert result["tools"][0]["success"] is False

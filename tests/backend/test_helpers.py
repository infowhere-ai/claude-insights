"""Unit tests for pure helper functions in app.py.

These tests exercise functions that have no side effects and require
no filesystem access, making them fast and fully isolated.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import app


# ── _tool_input_summary ───────────────────────────────────────────────────────

class TestToolInputSummary:
    def test_read_returns_file_path(self):
        assert app._tool_input_summary("Read", {"file_path": "/src/main.py"}) == "/src/main.py"

    def test_write_returns_file_path(self):
        assert app._tool_input_summary("Write", {"file_path": "/src/new.py"}) == "/src/new.py"

    def test_edit_returns_file_path(self):
        assert app._tool_input_summary("Edit", {"file_path": "/src/edit.py"}) == "/src/edit.py"

    def test_bash_returns_command_truncated(self):
        long_cmd = "x" * 100
        result = app._tool_input_summary("Bash", {"command": long_cmd})
        assert result == long_cmd[:80]

    def test_bash_returns_command_short(self):
        assert app._tool_input_summary("Bash", {"command": "ls -la"}) == "ls -la"

    def test_glob_returns_pattern(self):
        assert app._tool_input_summary("Glob", {"pattern": "**/*.py"}) == "**/*.py"

    def test_grep_returns_pattern(self):
        assert app._tool_input_summary("Grep", {"pattern": "def test_"}) == "def test_"

    def test_webfetch_returns_url(self):
        assert app._tool_input_summary("WebFetch", {"url": "https://example.com"}) == "https://example.com"

    def test_websearch_returns_query(self):
        assert app._tool_input_summary("WebSearch", {"query": "python asyncio"}) == "python asyncio"

    def test_generic_returns_first_string_value(self):
        result = app._tool_input_summary("Agent", {"description": "do something"})
        assert result == "do something"

    def test_generic_returns_empty_for_no_strings(self):
        result = app._tool_input_summary("Unknown", {"count": 42})
        assert result == ""

    def test_read_falls_back_to_path(self):
        assert app._tool_input_summary("Read", {"path": "/alt/path.py"}) == "/alt/path.py"


# ── _tool_detail ──────────────────────────────────────────────────────────────

class TestToolDetail:
    def test_bash_type(self):
        detail = app._tool_detail("Bash", {"command": "git status", "description": "Show git status"})
        assert detail["type"] == "bash"
        assert detail["command"] == "git status"
        assert detail["description"] == "Show git status"

    def test_bash_missing_description_is_empty(self):
        detail = app._tool_detail("Bash", {"command": "ls"})
        assert detail["description"] == ""

    def test_edit_type_with_diff(self):
        detail = app._tool_detail("Edit", {
            "file_path": "app.py",
            "old_string": "foo = 1\n",
            "new_string": "foo = 2\n",
        })
        assert detail["type"] == "edit"
        assert detail["file_path"] == "app.py"
        assert "-foo = 1" in detail["diff"]
        assert "+foo = 2" in detail["diff"]

    def test_edit_empty_strings_no_diff(self):
        detail = app._tool_detail("Edit", {"file_path": "a.py", "old_string": "", "new_string": ""})
        assert detail["type"] == "edit"
        assert detail["diff"] == ""

    def test_write_type(self):
        detail = app._tool_detail("Write", {"file_path": "out.py", "content": "print('hi')"})
        assert detail["type"] == "write"
        assert detail["file_path"] == "out.py"
        assert detail["content"] == "print('hi')"
        assert detail["total_chars"] == len("print('hi')")

    def test_write_content_truncated_at_3000(self):
        content = "x" * 5000
        detail = app._tool_detail("Write", {"file_path": "big.py", "content": content})
        assert len(detail["content"]) == 3000
        assert detail["total_chars"] == 5000

    def test_read_type(self):
        detail = app._tool_detail("Read", {"file_path": "src.py", "limit": 100, "offset": 50})
        assert detail["type"] == "read"
        assert detail["file_path"] == "src.py"
        assert detail["limit"] == 100
        assert detail["offset"] == 50

    def test_grep_type(self):
        detail = app._tool_detail("Grep", {"pattern": "TODO", "path": "/src"})
        assert detail["type"] == "search"
        assert detail["tool"] == "Grep"
        assert detail["pattern"] == "TODO"

    def test_glob_type(self):
        detail = app._tool_detail("Glob", {"pattern": "*.py", "path": "/src"})
        assert detail["type"] == "search"
        assert detail["tool"] == "Glob"

    def test_webfetch_type(self):
        detail = app._tool_detail("WebFetch", {"url": "https://example.com"})
        assert detail["type"] == "web"
        assert detail["url"] == "https://example.com"

    def test_websearch_type(self):
        detail = app._tool_detail("WebSearch", {"query": "fastapi docs"})
        assert detail["type"] == "web"
        assert detail["query"] == "fastapi docs"

    def test_agent_type(self):
        detail = app._tool_detail("Agent", {
            "description": "Run subagent",
            "prompt": "Do the thing",
        })
        assert detail["type"] == "agent"
        assert detail["description"] == "Run subagent"
        assert "Do the thing" in detail["prompt"]

    def test_generic_type_for_unknown_tool(self):
        detail = app._tool_detail("CustomTool", {"key": "value"})
        assert detail["type"] == "generic"
        assert "key" in detail["fields"]
        assert detail["fields"]["key"] == "value"


# ── _parse_skill_md ───────────────────────────────────────────────────────────

class TestParseSkillMd:
    def test_full_frontmatter(self):
        content = """---
name: my-skill
description: Does something useful
argument-hint: <project-name>
---

## How it works

Some body text here.
"""
        result = app._parse_skill_md(content, "my-skill")
        assert result["title"] == "my-skill"
        assert result["description"] == "Does something useful"
        assert result["argument_hint"] == "<project-name>"

    def test_no_frontmatter_uses_heading(self):
        content = """# My Skill Title

Some body text.
"""
        result = app._parse_skill_md(content, "my-skill")
        assert result["title"] == "My Skill Title"
        assert result["description"] == ""

    def test_body_intro_extracted(self):
        content = """---
name: test
description: desc
---

First paragraph of body text that is useful.

Second paragraph.
"""
        result = app._parse_skill_md(content, "test")
        assert "First paragraph" in result["body_intro"]
        assert "Second paragraph" not in result["body_intro"]

    def test_empty_content(self):
        result = app._parse_skill_md("", "fallback-name")
        assert result["name"] == "fallback-name"
        assert result["title"] == "fallback-name"
        assert result["description"] == ""

    def test_name_field_in_result(self):
        result = app._parse_skill_md("", "skill-xyz")
        assert result["name"] == "skill-xyz"


# ── _get_jsonl_dir ────────────────────────────────────────────────────────────

class TestGetJsonlDir:
    def test_encodes_slashes_as_dashes(self):
        project_path = Path("/home/user/my-project")
        result = app._get_jsonl_dir(project_path)
        assert result.name == "-home-user-my-project"

    def test_result_is_under_claude_projects(self):
        project_path = Path("/home/user/project")
        result = app._get_jsonl_dir(project_path)
        assert result.parent == Path.home() / ".claude" / "projects"

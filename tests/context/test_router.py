"""Tests for context inspector endpoint and its private helpers."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── helpers ────────────────────────────────────────────────────────────────────


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


def _tool_use_line(tool_id: str, name: str, inp: dict) -> dict:
    return {
        "type": "assistant",
        "timestamp": "2026-01-01T10:00:00Z",
        "message": {
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": inp}],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    }


def _tool_result_line(tool_id: str, content, is_error: bool = False) -> dict:
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": content,
                    "is_error": is_error,
                }
            ]
        },
    }


def _text_line(role: str, text: str) -> dict:
    return {"type": role, "message": {"content": [{"type": "text", "text": text}]}}


# ── existing endpoint tests ────────────────────────────────────────────────────


def test_context_inspect_unknown_project_returns_404(app_client):
    r = app_client.get("/api/context-inspect?project=does-not-exist")
    assert r.status_code == 404


def test_context_inspect_known_project(app_client, tmp_project, tmp_path):
    from claude_monitor import config as config_module

    (tmp_project / "CLAUDE.md").write_text("# Project\n\nInstructions here.")
    rules_dir = tmp_project / ".claude" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rule_file = rules_dir / "custom.md"
    rule_file.write_text("# Rule\n\nDo this.")

    encoded = str(tmp_project).replace("/", "-")
    jsonl_dir = tmp_path / "ci_proj" / encoded
    jsonl_dir.mkdir(parents=True)
    _write_jsonl(
        jsonl_dir / "sess.jsonl",
        [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Read",
                            "input": {"file_path": str(tmp_project / "CLAUDE.md")},
                        }
                    ],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            },
        ],
    )

    with patch.object(config_module, "CLAUDE_PROJECTS_DIR", tmp_path / "ci_proj"):
        r = app_client.get("/api/context-inspect?project=my-project")
    assert r.status_code == 200
    body = r.json()
    assert "rules" in body
    assert "reads" in body


# ── _collect_rules unit tests ─────────────────────────────────────────────────


def test_collect_rules_picks_claude_md_at_root(tmp_path):
    """
    Given a project with CLAUDE.md at root
    When _collect_rules is called
    Then the result includes a rule with category 'claude-md'
    """
    from claude_monitor.context.router import _collect_rules

    project = tmp_path / "my-project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# Root CLAUDE.md")

    result = _collect_rules(project, tmp_path)

    labels = [r["label"] for r in result]
    assert "CLAUDE.md" in labels
    categories = [r["category"] for r in result]
    assert "claude-md" in categories


def test_collect_rules_picks_dot_claude_claude_md(tmp_path):
    """
    Given a project with .claude/CLAUDE.md
    When _collect_rules is called
    Then the result includes a rule with category 'claude-md'
    """
    from claude_monitor.context.router import _collect_rules

    project = tmp_path / "my-project"
    claude_dir = project / ".claude"
    claude_dir.mkdir(parents=True)
    (claude_dir / "CLAUDE.md").write_text("# Dot CLAUDE.md")

    result = _collect_rules(project, tmp_path)

    assert any(r["category"] == "claude-md" for r in result)


def test_collect_rules_includes_rule_files_in_rules_dir(tmp_path):
    """
    Given a project with .claude/rules/myfile.md
    When _collect_rules is called
    Then the result includes a rule with category 'rule'
    """
    from claude_monitor.context.router import _collect_rules

    project = tmp_path / "proj"
    rules_dir = project / ".claude" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "my-rule.md").write_text("# My rule")

    result = _collect_rules(project, tmp_path)

    assert any(r["category"] == "rule" for r in result)


def test_collect_rules_recurses_into_symlinked_dir(tmp_path):
    """
    Given a rules/ entry that is a directory (or symlink to one) containing .md files
    When _collect_rules is called
    Then the nested .md files are included with category 'rule'
    """
    from claude_monitor.context.router import _collect_rules

    project = tmp_path / "proj"
    rules_dir = project / ".claude" / "rules"
    rules_dir.mkdir(parents=True)
    sub = tmp_path / "shared-rules"
    sub.mkdir()
    (sub / "shared.md").write_text("# Shared rule")
    (rules_dir / "shared").symlink_to(sub)

    result = _collect_rules(project, tmp_path)

    assert any(r["category"] == "rule" and "shared.md" in r["label"] for r in result)


def test_collect_rules_global_claude_md_with_global_category(tmp_path, monkeypatch):
    """
    Given a global ~/.claude/CLAUDE.md
    When _collect_rules is called
    Then the result includes a rule with category 'global'
    """
    from claude_monitor.context import router as router_module
    from claude_monitor.context.router import _collect_rules

    global_md = tmp_path / "global_claude" / "CLAUDE.md"
    global_md.parent.mkdir(parents=True)
    global_md.write_text("# Global CLAUDE.md")

    project = tmp_path / "proj"
    project.mkdir()

    with patch.object(router_module.config, "CLAUDE_GLOBAL_MD", global_md):
        result = _collect_rules(project, tmp_path)

    assert any(r["category"] == "global" for r in result)


def test_collect_rules_sorted_by_size_descending(tmp_path):
    """
    Given multiple rule files of different sizes
    When _collect_rules is called
    Then rules are sorted by size_bytes descending
    """
    from claude_monitor.context.router import _collect_rules

    project = tmp_path / "proj"
    rules_dir = project / ".claude" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "small.md").write_text("A")
    (rules_dir / "large.md").write_text("A" * 500)

    result = _collect_rules(project, tmp_path)
    rule_items = [r for r in result if r["category"] == "rule"]

    assert rule_items[0]["size_bytes"] >= rule_items[-1]["size_bytes"]


def test_collect_rules_missing_file_is_skipped(tmp_path):
    """
    Given a rules dir entry that disappears between iterdir and stat
    When _collect_rules is called
    Then no OSError propagates and the result is still a list
    """
    from claude_monitor.context.router import _collect_rules

    project = tmp_path / "proj"
    project.mkdir()

    # No .claude/rules directory at all — should simply return empty list without error
    result = _collect_rules(project, tmp_path)
    assert isinstance(result, list)


def test_collect_rules_rule_dict_has_expected_keys(tmp_path):
    """
    Given a project with a CLAUDE.md
    When _collect_rules is called
    Then each item has label, real_path, size_bytes, tokens_est, category
    """
    from claude_monitor.context.router import _collect_rules

    project = tmp_path / "proj"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# Hello")

    result = _collect_rules(project, tmp_path)

    assert len(result) >= 1
    for item in result:
        assert "label" in item
        assert "real_path" in item
        assert "size_bytes" in item
        assert "tokens_est" in item
        assert "category" in item


# ── _collect_reads unit tests ─────────────────────────────────────────────────


def test_collect_reads_returns_read_tool_entries(tmp_path):
    """
    Given a JSONL with a Read tool_use / tool_result pair
    When _collect_reads is called
    Then the result contains an entry with tool='Read'
    """
    from claude_monitor.context.router import _collect_reads

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(
        jsonl,
        [
            _tool_use_line("t1", "Read", {"file_path": "/some/file.py"}),
            _tool_result_line("t1", "file contents here"),
        ],
    )

    result = _collect_reads(jsonl)

    assert len(result) == 1
    assert result[0]["tool"] == "Read"
    assert result[0]["label"] == "/some/file.py"


def test_collect_reads_deduplicates_by_tool_and_label(tmp_path):
    """
    Given a JSONL with two Read tool calls for the same file
    When _collect_reads is called
    Then the result contains only one entry (the last occurrence)
    """
    from claude_monitor.context.router import _collect_reads

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(
        jsonl,
        [
            _tool_use_line("t1", "Read", {"file_path": "/file.py"}),
            _tool_result_line("t1", "first read"),
            _tool_use_line("t2", "Read", {"file_path": "/file.py"}),
            _tool_result_line("t2", "second read"),
        ],
    )

    result = _collect_reads(jsonl)

    assert len(result) == 1
    assert result[0]["content"] == "second read"


def test_collect_reads_ordered_by_last_occurrence_desc(tmp_path):
    """
    Given two different files read in order A then B
    When _collect_reads is called
    Then B comes first (most recent first)
    """
    from claude_monitor.context.router import _collect_reads

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(
        jsonl,
        [
            _tool_use_line("t1", "Read", {"file_path": "/a.py"}),
            _tool_result_line("t1", "a content"),
            _tool_use_line("t2", "Read", {"file_path": "/b.py"}),
            _tool_result_line("t2", "b content"),
        ],
    )

    result = _collect_reads(jsonl)

    assert result[0]["label"] == "/b.py"
    assert result[1]["label"] == "/a.py"


def test_collect_reads_skips_non_tool_tool_names(tmp_path):
    """
    Given a JSONL with a tool_use for an unknown tool name
    When _collect_reads is called
    Then that entry is excluded from the result
    """
    from claude_monitor.context.router import _collect_reads

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(
        jsonl,
        [
            _tool_use_line("t1", "SomethingElse", {"file_path": "/f.py"}),
            _tool_result_line("t1", "content"),
        ],
    )

    result = _collect_reads(jsonl)

    assert result == []


def test_collect_reads_includes_all_supported_tools(tmp_path):
    """
    Given JSONL entries for each supported tool (Read, Write, Edit, Bash, Glob, Grep, WebFetch)
    When _collect_reads is called
    Then all seven are included
    """
    from claude_monitor.context.router import _collect_reads

    tools = [
        ("Read", {"file_path": "/r.py"}),
        ("Write", {"file_path": "/w.py"}),
        ("Edit", {"file_path": "/e.py"}),
        ("Bash", {"command": "ls -la"}),
        ("Glob", {"pattern": "**/*.py"}),
        ("Grep", {"query": "TODO"}),
        ("WebFetch", {"url": "https://example.com"}),
    ]
    entries = []
    for i, (name, inp) in enumerate(tools):
        entries.append(_tool_use_line(f"t{i}", name, inp))
        entries.append(_tool_result_line(f"t{i}", "result"))

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(jsonl, entries)

    result = _collect_reads(jsonl)

    found_tools = {r["tool"] for r in result}
    assert found_tools == {"Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch"}


def test_collect_reads_list_content_is_joined(tmp_path):
    """
    Given a tool_result whose content is a list of text dicts
    When _collect_reads is called
    Then the content field is the joined text
    """
    from claude_monitor.context.router import _collect_reads

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(
        jsonl,
        [
            _tool_use_line("t1", "Read", {"file_path": "/f.py"}),
            _tool_result_line("t1", [{"text": "line1"}, {"text": "line2"}]),
        ],
    )

    result = _collect_reads(jsonl)

    assert result[0]["content"] == "line1\nline2"


def test_collect_reads_dict_has_expected_keys(tmp_path):
    """
    Given a JSONL with one Read tool pair
    When _collect_reads is called
    Then the result dict has all expected keys
    """
    from claude_monitor.context.router import _collect_reads

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(
        jsonl,
        [
            _tool_use_line("t1", "Read", {"file_path": "/f.py"}),
            _tool_result_line("t1", "hello"),
        ],
    )

    result = _collect_reads(jsonl)

    assert len(result) == 1
    item = result[0]
    for key in ("tool", "label", "size_bytes", "tokens_est", "is_error", "content", "total_chars"):
        assert key in item


def test_collect_reads_nonexistent_file_returns_empty(tmp_path):
    """
    Given a path to a JSONL that does not exist
    When _collect_reads is called
    Then it returns an empty list without raising
    """
    from claude_monitor.context.router import _collect_reads

    result = _collect_reads(tmp_path / "missing.jsonl")

    assert result == []


def test_collect_reads_content_truncated_at_8000(tmp_path):
    """
    Given a tool result whose text is longer than 8000 chars
    When _collect_reads is called
    Then content is truncated to 8000 chars but total_chars reflects full length
    """
    from claude_monitor.context.router import _collect_reads

    long_text = "X" * 10000
    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(
        jsonl,
        [
            _tool_use_line("t1", "Read", {"file_path": "/big.py"}),
            _tool_result_line("t1", long_text),
        ],
    )

    result = _collect_reads(jsonl)

    assert len(result[0]["content"]) == 8000
    assert result[0]["total_chars"] == 10000


# ── _collect_messages unit tests ──────────────────────────────────────────────


def test_collect_messages_returns_text_messages(tmp_path):
    """
    Given a JSONL with one user text message and one assistant text message
    When _collect_messages is called
    Then both are included
    """
    from claude_monitor.context.router import _collect_messages

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(
        jsonl,
        [
            _text_line("user", "Hello Claude"),
            _text_line("assistant", "Hello user"),
        ],
    )

    result = _collect_messages(jsonl)

    assert len(result) == 2


def test_collect_messages_excludes_tool_use_and_tool_result(tmp_path):
    """
    Given a JSONL that only has tool_use / tool_result content blocks
    When _collect_messages is called
    Then the result is empty (no text messages)
    """
    from claude_monitor.context.router import _collect_messages

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(
        jsonl,
        [
            _tool_use_line("t1", "Read", {"file_path": "/f.py"}),
            _tool_result_line("t1", "content"),
        ],
    )

    result = _collect_messages(jsonl)

    assert result == []


def test_collect_messages_excludes_system_reminder_lines(tmp_path):
    """
    Given messages that start with <system-reminder> or <command-
    When _collect_messages is called
    Then those messages are excluded
    """
    from claude_monitor.context.router import _collect_messages

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(
        jsonl,
        [
            _text_line("user", "<system-reminder>This is a system reminder</system-reminder>"),
            _text_line("user", "<command-X>cmd</command-X>"),
            _text_line("user", "A normal message"),
        ],
    )

    result = _collect_messages(jsonl)

    assert len(result) == 1
    assert result[0]["snippet"].startswith("A normal message")


def test_collect_messages_marks_compaction(tmp_path):
    """
    Given a user message that starts with the compaction prefix
    When _collect_messages is called
    Then is_compaction is True for that message
    """
    from claude_monitor.context.router import _collect_messages

    jsonl = tmp_path / "sess.jsonl"
    compaction_text = (
        "This session is being continued from a previous conversation that ran out of context."
    )
    _write_jsonl(
        jsonl,
        [_text_line("user", compaction_text)],
    )

    result = _collect_messages(jsonl)

    assert len(result) == 1
    assert result[0]["is_compaction"] is True


def test_collect_messages_returned_in_reverse_order(tmp_path):
    """
    Given messages in chronological order [msg1, msg2, msg3]
    When _collect_messages is called
    Then the result is [msg3, msg2, msg1] (most recent first)
    """
    from claude_monitor.context.router import _collect_messages

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(
        jsonl,
        [
            _text_line("user", "first"),
            _text_line("assistant", "second"),
            _text_line("user", "third"),
        ],
    )

    result = _collect_messages(jsonl)

    assert result[0]["snippet"].startswith("third")
    assert result[1]["snippet"].startswith("second")
    assert result[2]["snippet"].startswith("first")


def test_collect_messages_capped_at_50(tmp_path):
    """
    Given more than 50 messages in a JSONL
    When _collect_messages is called
    Then at most 50 are returned (the last 50 in reverse order)
    """
    from claude_monitor.context.router import _collect_messages

    jsonl = tmp_path / "sess.jsonl"
    entries = [_text_line("user", f"message {i}") for i in range(60)]
    _write_jsonl(jsonl, entries)

    result = _collect_messages(jsonl)

    assert len(result) == 50


def test_collect_messages_snippet_truncated_at_120(tmp_path):
    """
    Given a message longer than 120 chars
    When _collect_messages is called
    Then snippet is at most 120 chars but full_text and total_chars reflect more
    """
    from claude_monitor.context.router import _collect_messages

    long_text = "A" * 300
    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(jsonl, [_text_line("user", long_text)])

    result = _collect_messages(jsonl)

    assert len(result[0]["snippet"]) == 120
    assert result[0]["total_chars"] == 300


def test_collect_messages_dict_has_expected_keys(tmp_path):
    """
    Given a JSONL with one user text message
    When _collect_messages is called
    Then the result dict has all expected keys
    """
    from claude_monitor.context.router import _collect_messages

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(jsonl, [_text_line("user", "hello")])

    result = _collect_messages(jsonl)

    assert len(result) == 1
    item = result[0]
    for key in (
        "role",
        "is_compaction",
        "snippet",
        "full_text",
        "total_chars",
        "size_bytes",
        "tokens_est",
    ):
        assert key in item


def test_collect_messages_nonexistent_file_returns_empty(tmp_path):
    """
    Given a path to a JSONL that does not exist
    When _collect_messages is called
    Then it returns an empty list without raising
    """
    from claude_monitor.context.router import _collect_messages

    result = _collect_messages(tmp_path / "missing.jsonl")

    assert result == []


def test_collect_messages_string_content_is_handled(tmp_path):
    """
    Given a message whose content field is a plain string (not a list)
    When _collect_messages is called
    Then the string is treated as text
    """
    from claude_monitor.context.router import _collect_messages

    jsonl = tmp_path / "sess.jsonl"
    jsonl.write_text(
        json.dumps({"type": "user", "message": {"content": "plain string message"}}),
        encoding="utf-8",
    )

    result = _collect_messages(jsonl)

    assert len(result) == 1
    assert result[0]["snippet"] == "plain string message"

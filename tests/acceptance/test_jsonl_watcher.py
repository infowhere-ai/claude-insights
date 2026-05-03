"""
Acceptance tests — JSONL Watcher.

Spec: standarts/private/projects/claude-monitor/specs/jsonl-watcher.md
Product Owner: Leandro Siciliano | Date: 2026-05-01
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from claude_monitor.jsonl import parser


def _write_jsonl(directory: Path, filename: str, messages: list) -> Path:
    f = directory / filename
    f.write_text("\n".join(json.dumps(m) for m in messages), encoding="utf-8")
    return f


class TestAcceptanceJsonlWatcher:

    def test_last_tool_extracted_from_tail(self, tmp_jsonl_dir):
        """
        Given  the JSONL contains an assistant message with tool_use name="Bash"
        When   parse_jsonl_tail is called
        Then   the returned dict contains tool="Bash"
        """
        jsonl = _write_jsonl(tmp_jsonl_dir, "session.jsonl", [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 100, "output_tokens": 50,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Bash",
                         "input": {"command": "ls -la"}},
                    ],
                },
            },
        ])

        result = parser.parse_jsonl_tail(jsonl)

        assert result.get("tool") == "Bash"

    def test_thinking_block_detected(self, tmp_jsonl_dir):
        """
        Given  the JSONL contains a non-empty thinking block
        When   detect_latest_thinking is called
        Then   returns a dict with text, word_count and block_id
        """
        jsonl = _write_jsonl(tmp_jsonl_dir, "think_session.jsonl", [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 10, "output_tokens": 5,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                    "content": [
                        {"type": "thinking", "thinking": "I need to think carefully about this."},
                        {"type": "text", "text": "Here is my answer."},
                    ],
                },
            },
        ])

        result = parser.detect_latest_thinking(jsonl)

        assert result is not None
        assert result["text"] == "I need to think carefully about this."
        assert result["word_count"] == 7
        assert "block_id" in result
        assert len(result["block_id"]) == 12

    def test_empty_thinking_block_not_returned(self, tmp_jsonl_dir):
        """
        Given  the JSONL contains a thinking block that is empty or whitespace only
        When   detect_latest_thinking is called
        Then   returns None
        """
        jsonl = _write_jsonl(tmp_jsonl_dir, "empty_think.jsonl", [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T11:00:00Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 10, "output_tokens": 5,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                    "content": [{"type": "thinking", "thinking": "   "}],
                },
            },
        ])

        result = parser.detect_latest_thinking(jsonl)
        assert result is None

    def test_invalid_json_line_in_tail_does_not_raise(self, tmp_jsonl_dir):
        """
        Given  the JSONL tail contains a malformed line
        When   parse_jsonl_tail is called
        Then   does not raise and the invalid line is ignored
        """
        jsonl = tmp_jsonl_dir / "corrupt.jsonl"
        lines = [
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 10, "output_tokens": 5,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                    "content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                  "input": {"file_path": "/foo.py"}}],
                },
            }),
            "{{INVALID JSON LINE}}",
        ]
        jsonl.write_text("\n".join(lines), encoding="utf-8")

        result = parser.parse_jsonl_tail(jsonl)
        assert isinstance(result, dict)

    def test_thinking_block_same_timestamp_has_same_block_id(self, tmp_jsonl_dir):
        """
        Given  two thinking blocks share the same timestamp
        When   detect_latest_thinking is called on each
        Then   both have the same block_id (stable per timestamp)
        """
        ts = "2026-01-01T10:00:00Z"
        jsonl1 = _write_jsonl(tmp_jsonl_dir, "think_a.jsonl", [
            {"type": "assistant", "timestamp": ts,
             "message": {"model": "m", "usage": {"input_tokens": 1, "output_tokens": 1,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                          "content": [{"type": "thinking", "thinking": "First thought"}]}},
        ])
        jsonl2 = _write_jsonl(tmp_jsonl_dir, "think_b.jsonl", [
            {"type": "assistant", "timestamp": ts,
             "message": {"model": "m", "usage": {"input_tokens": 1, "output_tokens": 1,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                          "content": [{"type": "thinking", "thinking": "Second thought"}]}},
        ])

        r1 = parser.detect_latest_thinking(jsonl1)
        r2 = parser.detect_latest_thinking(jsonl2)

        assert r1 is not None and r2 is not None
        assert r1["block_id"] == r2["block_id"], "Same timestamp should produce same block_id"

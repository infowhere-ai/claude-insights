"""
Acceptance tests — JSONL Watcher.

Spec: standarts/private/projects/claude-monitor/specs/jsonl-watcher.md
Product Owner: Leandro Siciliano | Data: 2026-05-01
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _write_jsonl(directory: Path, filename: str, messages: list) -> Path:
    f = directory / filename
    f.write_text("\n".join(json.dumps(m) for m in messages), encoding="utf-8")
    return f


class TestAcceptanceJsonlWatcher:

    def test_last_tool_extracted_from_tail(self, tmp_jsonl_dir):
        """
        Given that   o JSONL contém um assistant message com tool_use name="Bash"
        When     _parse_jsonl_tail é chamado
        Then      o dict retornado contém tool="Bash"
        """
        import importlib
        import db as db_module
        import app as app_module
        importlib.reload(db_module)
        importlib.reload(app_module)

        # Arrange
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

        # Act
        result = app_module._parse_jsonl_tail(jsonl)

        # Assert
        assert result.get("tool") == "Bash", (
            f"Expected tool='Bash', got: {result.get('tool')!r}"
        )

    def test_thinking_block_detected(self, tmp_jsonl_dir):
        """
        Given that   o JSONL contém um thinking block não vazio
        When     _detect_latest_thinking é chamado
        Then      retorna dict com text, word_count e block_id
        """
        import importlib
        import db as db_module
        import app as app_module
        importlib.reload(db_module)
        importlib.reload(app_module)

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

        # Act
        result = app_module._detect_latest_thinking(jsonl)

        # Assert
        assert result is not None, "Should return thinking block"
        assert result["text"] == "I need to think carefully about this."
        assert result["word_count"] == 7
        assert "block_id" in result, "block_id should be present"
        assert len(result["block_id"]) == 12, "block_id should be 12 chars (MD5 prefix)"

    def test_empty_thinking_block_not_returned(self, tmp_jsonl_dir):
        """
        Given that   o JSONL contém um thinking block vazio ou só whitespace
        When     _detect_latest_thinking é chamado
        Then      retorna None
        """
        import importlib
        import db as db_module
        import app as app_module
        importlib.reload(db_module)
        importlib.reload(app_module)

        jsonl = _write_jsonl(tmp_jsonl_dir, "empty_think.jsonl", [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T11:00:00Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 10, "output_tokens": 5,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                    "content": [
                        {"type": "thinking", "thinking": "   "},
                    ],
                },
            },
        ])

        # Act
        result = app_module._detect_latest_thinking(jsonl)

        # Assert
        assert result is None, "Empty/whitespace thinking block should return None"

    def test_invalid_json_line_in_tail_does_not_raise(self, tmp_jsonl_dir):
        """
        Given that   o tail do JSONL contém uma linha malformada
        When     _parse_jsonl_tail é chamado
        Then      não levanta excepção e a linha inválida é ignorada
        """
        import importlib
        import db as db_module
        import app as app_module
        importlib.reload(db_module)
        importlib.reload(app_module)

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

        # Act — must not raise
        result = app_module._parse_jsonl_tail(jsonl)

        # Assert — parsing continued; tool may be found or not, no exception
        assert isinstance(result, dict), "Should return a dict even with corrupt lines"

    def test_thinking_block_same_timestamp_has_same_block_id(self, tmp_jsonl_dir):
        """
        Given that   dois thinking blocks têm o mesmo timestamp
        When     _detect_latest_thinking é chamado
        Then      ambos têm o mesmo block_id (estável por timestamp)
        """
        import importlib
        import db as db_module
        import app as app_module
        importlib.reload(db_module)
        importlib.reload(app_module)

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

        r1 = app_module._detect_latest_thinking(jsonl1)
        r2 = app_module._detect_latest_thinking(jsonl2)

        assert r1 is not None and r2 is not None
        assert r1["block_id"] == r2["block_id"], (
            "Same timestamp should produce same block_id (stable for deduplication)"
        )

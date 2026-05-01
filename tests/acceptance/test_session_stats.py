"""
Acceptance tests — Session Stats.

Spec: standarts/private/projects/claude-monitor/specs/session-stats.md
Product Owner: Leandro Siciliano | Data: 2026-05-01
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _setup_project_stats(tmp_path, monkeypatch, messages: list):
    """Helper: creates project dir + JSONL in the actual expected location for _get_project_stats."""
    import importlib
    import db as db_module
    import app as app_module
    importlib.reload(db_module)
    importlib.reload(app_module)

    project_dir = tmp_path / "my-project"
    project_dir.mkdir()

    # _get_project_stats looks for ~/.claude/projects/<encoded>/*.jsonl
    # where encoded = str(project_path).replace("/", "-")
    encoded = str(project_dir).replace("/", "-")
    jsonl_dir = Path.home() / ".claude" / "projects" / encoded
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    session = jsonl_dir / "test_acceptance_session.jsonl"
    session.write_text(
        "\n".join(json.dumps(m) for m in messages),
        encoding="utf-8",
    )

    # Note: session file written to ~/.claude/projects/<encoded>/test_acceptance_session.jsonl
    # It will be cleaned up by future runs or manually

    return project_dir, session, app_module


class TestAcceptanceSessionStats:

    def test_session_ctx_tokens_computed_correctly(self, tmp_path, monkeypatch):
        """
        Given that   o JSONL tem usage: {input: 100, output: 50, cache_read: 200, cache_creation: 0}
        When     _get_project_stats é chamado
        Then      session_ctx_tokens = 300 (input + cache_read + cache_creation)
                   e session_output_tokens = 50
        """
        # Arrange
        messages = [{
            "type": "assistant",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 0,
                },
                "content": [{"type": "text", "text": "Hello"}],
            },
        }]
        project_dir, _, app_module = _setup_project_stats(tmp_path, monkeypatch, messages)

        # Act
        stats = app_module._get_project_stats(project_dir, "my-project")

        # Assert
        assert stats["session_output_tokens"] == 50
        ctx = stats["session_ctx_tokens"]
        assert ctx == 300, f"Expected session_ctx_tokens=300 (100+200+0), got {ctx}"

    def test_stats_cached_when_mtime_unchanged(self, tmp_path, monkeypatch):
        """
        Given that   o JSONL foi parseado e as stats cacheadas
        When     _get_project_stats é chamado novamente sem mudança de mtime
        Then      o cache é usado (stats retornadas são as mesmas)
        """
        messages = [{
            "type": "assistant",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 50, "output_tokens": 20,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                "content": [{"type": "text", "text": "hello"}],
            },
        }]
        project_dir, jsonl, app_module = _setup_project_stats(tmp_path, monkeypatch, messages)

        # First call — populates cache
        stats1 = app_module._get_project_stats(project_dir, "my-project")
        assert stats1["session_output_tokens"] == 20

        # Second call — same mtime → should return cached stats
        stats2 = app_module._get_project_stats(project_dir, "my-project")
        assert stats1["session_output_tokens"] == stats2["session_output_tokens"]
        # Cache key should be in _project_stats_cache
        assert "my-project" in app_module._project_stats_cache

    def test_model_detected_from_last_assistant_message(self, tmp_path, monkeypatch):
        """
        Given that   o JSONL tem um assistant message com model="claude-sonnet-4-6"
        When     _get_project_stats é chamado
        Then      stats["model"] = "claude-sonnet-4-6"
        """
        messages = [{
            "type": "assistant",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 10, "output_tokens": 5,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                "content": [{"type": "text", "text": "Hello"}],
            },
        }]
        project_dir, _, app_module = _setup_project_stats(tmp_path, monkeypatch, messages)

        stats = app_module._get_project_stats(project_dir, "my-project")
        assert stats.get("model") == "claude-sonnet-4-6", (
            f"Expected model='claude-sonnet-4-6', got: {stats.get('model')!r}"
        )

    def test_stats_endpoint_returns_token_data(self, app_client, tmp_project):
        """
        Given that   o projecto está registado e tem stats
        When     GET /api/insights-stats?project=<name>
        Then      a resposta contém campos de tokens
        """
        # Act
        r = app_client.get(f"/api/insights-stats?project={tmp_project.name}")
        assert r.status_code == 200

        data = r.json()
        # Assert — fields present (may be 0 if no JSONL, but keys should exist)
        assert "sessions_count" in data or "total_tokens" in data or "cache_hit_pct" in data, (
            f"Token stats fields missing from response: {list(data.keys())}"
        )

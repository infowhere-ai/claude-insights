"""
Acceptance tests — Session Stats.

Spec: standarts/private/projects/claude-monitor/specs/session-stats.md
Product Owner: Leandro Siciliano | Date: 2026-05-01
"""

import importlib
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _setup_project_stats(tmp_path, monkeypatch, messages: list):
    """Create project dir + JSONL in the expected location for get_project_stats."""
    import claude_monitor.db as db_module
    import claude_monitor.config as config_module
    import claude_monitor.state as state_module

    importlib.reload(db_module)
    importlib.reload(config_module)
    importlib.reload(state_module)

    from claude_monitor.stats import service as stats_service

    project_dir = tmp_path / "my-project"
    project_dir.mkdir(exist_ok=True)

    encoded = str(project_dir).replace("/", "-")
    jsonl_dir = Path.home() / ".claude" / "projects" / encoded
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    session = jsonl_dir / "test_acceptance_session.jsonl"
    session.write_text(
        "\n".join(json.dumps(m) for m in messages),
        encoding="utf-8",
    )

    return project_dir, session, stats_service, state_module


class TestAcceptanceSessionStats:
    def test_session_ctx_tokens_computed_correctly(self, tmp_path, monkeypatch):
        """
        Given  the JSONL has usage: {input: 100, output: 50, cache_read: 200, cache_creation: 0}
        When   get_project_stats is called
        Then   session_ctx_tokens = 300 (input + cache_read + cache_creation)
        And    session_output_tokens = 50
        """
        messages = [
            {
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
            }
        ]
        project_dir, _, stats_service, _ = _setup_project_stats(tmp_path, monkeypatch, messages)

        stats = stats_service.get_project_stats(project_dir, "my-project")

        assert stats["session_output_tokens"] == 50
        ctx = stats["session_ctx_tokens"]
        assert ctx == 300, f"Expected session_ctx_tokens=300 (100+200+0), got {ctx}"

    def test_stats_cached_when_mtime_unchanged(self, tmp_path, monkeypatch):
        """
        Given  the JSONL was parsed and stats cached
        When   get_project_stats is called again without mtime change
        Then   the cache is used (returned stats are identical)
        """
        messages = [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 50,
                        "output_tokens": 20,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    "content": [{"type": "text", "text": "hello"}],
                },
            }
        ]
        project_dir, _, stats_service, state_module = _setup_project_stats(
            tmp_path, monkeypatch, messages
        )

        stats1 = stats_service.get_project_stats(project_dir, "my-project")
        assert stats1["session_output_tokens"] == 20

        stats2 = stats_service.get_project_stats(project_dir, "my-project")
        assert stats1["session_output_tokens"] == stats2["session_output_tokens"]
        assert "my-project" in state_module._project_stats_cache

    def test_model_detected_from_last_assistant_message(self, tmp_path, monkeypatch):
        """
        Given  the JSONL has an assistant message with model="claude-sonnet-4-6"
        When   get_project_stats is called
        Then   stats["model"] = "claude-sonnet-4-6"
        """
        messages = [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    "content": [{"type": "text", "text": "Hello"}],
                },
            }
        ]
        project_dir, _, stats_service, _ = _setup_project_stats(tmp_path, monkeypatch, messages)

        stats = stats_service.get_project_stats(project_dir, "my-project")
        assert stats.get("model") == "claude-sonnet-4-6"

    def test_stats_endpoint_returns_token_data(self, app_client, tmp_project):
        """
        Given  the project is registered and has stats
        When   GET /api/insights-stats?project=<name>
        Then   the response contains token fields
        """
        r = app_client.get(f"/api/insights-stats?project={tmp_project.name}")
        assert r.status_code == 200

        data = r.json()
        assert "sessions_count" in data or "total_tokens" in data or "cache_hit_pct" in data

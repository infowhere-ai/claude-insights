"""Tests for session and agent history endpoints."""

import json
import sys
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


def _assistant_entry(tool: str = "Read", input_tokens: int = 100,
                     output_tokens: int = 50) -> dict:
    return {
        "type": "assistant",
        "timestamp": "2026-01-01T10:00:00Z",
        "message": {
            "model": "claude-sonnet-4-6",
            "content": [{"type": "tool_use", "id": "t1", "name": tool, "input": {}}],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 0,
            },
        },
    }


def test_sessions_unknown_project_returns_404(app_client):
    r = app_client.get("/api/sessions?project=does-not-exist")
    assert r.status_code == 404


def test_session_detail_unknown_project_returns_404(app_client):
    r = app_client.get("/api/session-detail?project=does-not-exist&session_id=abc")
    assert r.status_code == 404


def test_agent_history_returns_list(app_client):
    r = app_client.get("/api/agent-history")
    assert r.status_code == 200
    body = r.json()
    assert "agents" in body
    assert isinstance(body["agents"], list)


def test_agent_history_accepts_project_filter(app_client):
    r = app_client.get("/api/agent-history?project=my-project")
    assert r.status_code == 200
    assert "agents" in r.json()


def test_agent_history_accepts_limit(app_client):
    r = app_client.get("/api/agent-history?limit=10")
    assert r.status_code == 200
    assert "agents" in r.json()


def test_session_history_returns_list(app_client):
    r = app_client.get("/api/session-history")
    assert r.status_code == 200
    body = r.json()
    assert "sessions" in body
    assert isinstance(body["sessions"], list)


def test_session_history_accepts_project_filter(app_client):
    r = app_client.get("/api/session-history?project=my-project")
    assert r.status_code == 200
    assert "sessions" in r.json()


def test_session_history_accepts_limit(app_client):
    r = app_client.get("/api/session-history?limit=5")
    assert r.status_code == 200
    assert "sessions" in r.json()


class TestSessionsKnownProject:
    def test_sessions_known_project_returns_list(self, app_client, tmp_project):
        from claude_monitor.sessions import service as session_service
        with patch.object(session_service, "list_sessions", return_value=[
            {"session_id": "abc123", "is_active": True, "started_at": "2026-01-01T10:00:00Z"}
        ]):
            r = app_client.get("/api/sessions?project=my-project")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert data[0]["session_id"] == "abc123"


class TestSessionDetailEndpoint:
    def test_session_detail_with_valid_jsonl(self, app_client, tmp_project, tmp_path):
        from claude_monitor import config as config_module
        project_path = tmp_project
        encoded = str(project_path).replace("/", "-")
        jsonl_dir = tmp_path / "claude_proj" / encoded
        jsonl_dir.mkdir(parents=True)
        jsonl_file = jsonl_dir / "test_sess.jsonl"
        _write_jsonl(jsonl_file, [_assistant_entry("Read")])

        with patch.object(config_module, "CLAUDE_PROJECTS_DIR", tmp_path / "claude_proj"):
            r = app_client.get("/api/session-detail?project=my-project&session_id=test_sess")
        assert r.status_code == 200
        body = r.json()
        assert "tools" in body
        assert "thinking" in body
        assert "stats" in body

    def test_session_detail_missing_jsonl_returns_404(self, app_client, tmp_project, tmp_path):
        from claude_monitor import config as config_module
        with patch.object(config_module, "CLAUDE_PROJECTS_DIR", tmp_path / "nonexistent"):
            r = app_client.get("/api/session-detail?project=my-project&session_id=ghost")
        assert r.status_code == 404

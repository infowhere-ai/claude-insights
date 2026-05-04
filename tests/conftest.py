"""Shared pytest fixtures for claude-monitor tests."""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── Filesystem fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def tmp_projects_root(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    return root


@pytest.fixture
def tmp_project(tmp_projects_root):
    project = tmp_projects_root / "my-project"
    claude_dir = project / ".claude"
    claude_dir.mkdir(parents=True)
    status = {"status": "idle", "ts": "2026-01-01T00:00:00Z"}
    (claude_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    return project


@pytest.fixture
def tmp_jsonl_dir(tmp_path):
    d = tmp_path / "claude_projects" / "-home-user-my-project"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def sample_jsonl(tmp_jsonl_dir):
    session = tmp_jsonl_dir / "abc123.jsonl"
    lines = [
        json.dumps(
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
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_001",
                            "name": "Read",
                            "input": {"file_path": "/home/user/my-project/app.py"},
                        }
                    ],
                },
            }
        ),
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-01-01T10:00:05Z",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_001",
                            "content": "file content here",
                            "is_error": False,
                        }
                    ]
                },
            }
        ),
    ]
    session.write_text("\n".join(lines), encoding="utf-8")
    return session


@pytest.fixture
def sample_thinking_jsonl(tmp_jsonl_dir):
    session = tmp_jsonl_dir / "think123.jsonl"
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-01-01T11:00:00Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "I need to carefully analyse the problem.",
                        },
                        {"type": "text", "text": "Here is my answer."},
                    ],
                },
            }
        ),
    ]
    session.write_text("\n".join(lines), encoding="utf-8")
    return session


# ── App fixture (with patched PROJECTS_ROOT) ──────────────────────────────────


@pytest.fixture
def app_client(tmp_projects_root, tmp_project, tmp_path, monkeypatch):
    """FastAPI TestClient with PROJECTS_ROOT pointed at tmp_projects_root."""
    from fastapi.testclient import TestClient
    import importlib

    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_projects_root))
    monkeypatch.setenv("CLAUDE_INSIGHTS_DB", str(tmp_path / "test-insights.db"))

    import claude_monitor.db as db_module
    import claude_monitor.config as config_module
    import claude_monitor.state as state_module
    import claude_monitor.main as app_module

    importlib.reload(db_module)
    importlib.reload(config_module)
    importlib.reload(state_module)
    importlib.reload(app_module)

    from claude_monitor.core import background as background_module

    async def _noop():
        pass

    monkeypatch.setattr(background_module, "discovery_loop", _noop)
    monkeypatch.setattr(background_module, "poll_loop", _noop)
    monkeypatch.setattr(background_module, "jsonl_watcher_loop", _noop)

    with TestClient(app_module.app, raise_server_exceptions=True) as client:
        yield client

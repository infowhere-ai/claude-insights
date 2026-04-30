"""Shared pytest fixtures for claude-insights tests."""

import json
import os
import sys
from pathlib import Path

import pytest

# Add project root to sys.path so we can import app.py directly
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── Filesystem fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def tmp_projects_root(tmp_path):
    """A temporary directory acting as PROJECTS_ROOT with one project inside."""
    root = tmp_path / "projects"
    root.mkdir()
    return root


@pytest.fixture
def tmp_project(tmp_projects_root):
    """A single project directory with .claude/ and status.json."""
    project = tmp_projects_root / "my-project"
    claude_dir = project / ".claude"
    claude_dir.mkdir(parents=True)
    status = {
        "status": "idle",
        "ts": "2026-01-01T00:00:00Z",
    }
    (claude_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    return project


@pytest.fixture
def tmp_jsonl_dir(tmp_path):
    """A directory simulating ~/.claude/projects/<encoded>/."""
    d = tmp_path / "claude_projects" / "-home-user-my-project"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def sample_jsonl(tmp_jsonl_dir):
    """A minimal JSONL session file with one assistant message and one tool use."""
    session = tmp_jsonl_dir / "abc123.jsonl"
    lines = [
        json.dumps({
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
        }),
        json.dumps({
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
        }),
    ]
    session.write_text("\n".join(lines), encoding="utf-8")
    return session


@pytest.fixture
def sample_thinking_jsonl(tmp_jsonl_dir):
    """A JSONL file with a thinking block."""
    session = tmp_jsonl_dir / "think123.jsonl"
    lines = [
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-01-01T11:00:00Z",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 10, "output_tokens": 5,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                "content": [
                    {"type": "thinking", "thinking": "I need to carefully analyse the problem."},
                    {"type": "text", "text": "Here is my answer."},
                ],
            },
        }),
    ]
    session.write_text("\n".join(lines), encoding="utf-8")
    return session


# ── App fixture (with patched PROJECTS_ROOT) ──────────────────────────────────

@pytest.fixture
def app_client(tmp_projects_root, tmp_project, tmp_path, monkeypatch):
    """FastAPI TestClient with PROJECTS_ROOT pointed at tmp_projects_root.

    Startup background tasks (discovery_loop, poll_loop, jsonl_watcher_loop)
    are monkey-patched to no-ops so tests don't run infinite loops.
    SQLite DB is isolated to a temp path so real ~/.claude/claude-insights.db
    is never touched.
    """
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_projects_root))
    monkeypatch.setenv("CLAUDE_INSIGHTS_DB", str(tmp_path / "test-insights.db"))

    # Reload app module so PROJECTS_ROOT and db module constants are re-evaluated
    import importlib
    import db as db_module
    import app as app_module
    importlib.reload(db_module)
    importlib.reload(app_module)

    # Patch background tasks to prevent infinite loops in test
    async def _noop(): pass
    monkeypatch.setattr(app_module, "discovery_loop", _noop)
    monkeypatch.setattr(app_module, "poll_loop", _noop)
    monkeypatch.setattr(app_module, "jsonl_watcher_loop", _noop)

    with TestClient(app_module.app, raise_server_exceptions=True) as client:
        yield client

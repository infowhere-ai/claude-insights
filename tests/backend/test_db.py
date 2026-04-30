"""Unit tests for db.py — SQLite persistence layer.

Uses a temporary database (CLAUDE_INSIGHTS_DB env var) so tests never touch
the real ~/.claude/claude-insights.db.
"""

import datetime
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path):
    """Isolated SQLite DB per test. Returns the Path to the db file."""
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)
    return db_path


def _make_agent(agent_id="agent_abc123", state="done", tools=3,
                started="2026-01-01T10:00:00Z", finished="2026-01-01T10:05:00Z"):
    return {
        "id": agent_id,
        "state": state,
        "started_at": started,
        "finished_at": finished,
        "description": "Write tests for the auth module",
        "tools_used": tools,
    }


def _make_stats(started="2026-01-01T10:00:00Z"):
    return {
        "started_at": started,
        "session_input_tokens": 1000,
        "session_output_tokens": 500,
        "session_cache_read": 200,
        "session_ctx_tokens": 4000,
        "top_tool": "Read",
        "tool_call_count": 12,
    }


# ── init_db ───────────────────────────────────────────────────────────────────

def test_init_db_creates_tables(tmp_db):
    import sqlite3
    conn = sqlite3.connect(str(tmp_db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "agent_runs" in tables
    assert "session_runs" in tables


def test_init_db_is_idempotent(tmp_db):
    """Calling init_db twice must not raise."""
    db.init_db(db_path=tmp_db)
    db.init_db(db_path=tmp_db)


# ── upsert_agent_run ──────────────────────────────────────────────────────────

def test_upsert_agent_run_inserts(tmp_db):
    agent = _make_agent()
    db.upsert_agent_run(agent, project="my-project", session_id="sess_01", db_path=tmp_db)
    rows = db.get_agent_history(project="my-project", db_path=tmp_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "agent_abc123"
    assert row["project"] == "my-project"
    assert row["session_id"] == "sess_01"
    assert row["description"] == "Write tests for the auth module"
    assert row["tools_used"] == 3


def test_upsert_agent_run_calculates_duration(tmp_db):
    agent = _make_agent(started="2026-01-01T10:00:00Z", finished="2026-01-01T10:05:00Z")
    db.upsert_agent_run(agent, project="p", db_path=tmp_db)
    rows = db.get_agent_history(project="p", db_path=tmp_db)
    assert rows[0]["duration_seconds"] == 300


def test_upsert_agent_run_is_idempotent(tmp_db):
    """Calling upsert twice with the same agent must result in one row."""
    agent = _make_agent()
    db.upsert_agent_run(agent, project="p", db_path=tmp_db)
    db.upsert_agent_run(agent, project="p", db_path=tmp_db)
    rows = db.get_agent_history(project="p", db_path=tmp_db)
    assert len(rows) == 1


def test_upsert_agent_run_updates_finished_at(tmp_db):
    """Second upsert with finished_at fills in previously missing value."""
    agent_running = {"id": "agent_xyz", "state": "running",
                     "started_at": "2026-01-01T10:00:00Z",
                     "finished_at": None, "description": "do stuff", "tools_used": 0}
    db.upsert_agent_run(agent_running, project="p", db_path=tmp_db)

    agent_done = dict(agent_running)
    agent_done["state"] = "done"
    agent_done["finished_at"] = "2026-01-01T10:02:00Z"
    agent_done["tools_used"] = 5
    db.upsert_agent_run(agent_done, project="p", db_path=tmp_db)

    rows = db.get_agent_history(project="p", db_path=tmp_db)
    assert len(rows) == 1
    assert rows[0]["finished_at"] == "2026-01-01T10:02:00Z"
    assert rows[0]["tools_used"] == 5


def test_upsert_agent_run_handles_missing_timestamps(tmp_db):
    """Agent with no timestamps must not raise."""
    agent = {"id": "agent_notimestamp", "state": "done", "description": "x", "tools_used": 0}
    db.upsert_agent_run(agent, project="p", db_path=tmp_db)
    rows = db.get_agent_history(project="p", db_path=tmp_db)
    assert len(rows) == 1
    assert rows[0]["duration_seconds"] is None


def test_upsert_agent_run_same_id_different_project(tmp_db):
    """Same agent ID in two projects must produce two rows."""
    agent = _make_agent()
    db.upsert_agent_run(agent, project="project-a", db_path=tmp_db)
    db.upsert_agent_run(agent, project="project-b", db_path=tmp_db)
    all_rows = db.get_agent_history(db_path=tmp_db)
    assert len(all_rows) == 2


# ── upsert_session_run ────────────────────────────────────────────────────────

def test_upsert_session_run_inserts(tmp_db):
    stats = _make_stats()
    db.upsert_session_run("sess_01", "my-project", stats,
                          finished_at="2026-01-01T11:00:00Z", agent_count=3, db_path=tmp_db)
    rows = db.get_session_history(project="my-project", db_path=tmp_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "sess_01"
    assert row["project"] == "my-project"
    assert row["input_tokens"] == 1000
    assert row["output_tokens"] == 500
    assert row["cache_read_tokens"] == 200
    assert row["context_tokens"] == 4000
    assert row["top_tool"] == "Read"
    assert row["tool_call_count"] == 12
    assert row["agent_count"] == 3


def test_upsert_session_run_is_idempotent(tmp_db):
    stats = _make_stats()
    db.upsert_session_run("sess_01", "p", stats, db_path=tmp_db)
    db.upsert_session_run("sess_01", "p", stats, db_path=tmp_db)
    rows = db.get_session_history(project="p", db_path=tmp_db)
    assert len(rows) == 1


def test_upsert_session_run_updates_token_counts(tmp_db):
    """Token counts should be overwritten on conflict (latest wins)."""
    stats1 = _make_stats()
    db.upsert_session_run("sess_01", "p", stats1, db_path=tmp_db)

    stats2 = dict(stats1)
    stats2["session_input_tokens"] = 9999
    db.upsert_session_run("sess_01", "p", stats2, db_path=tmp_db)

    rows = db.get_session_history(project="p", db_path=tmp_db)
    assert rows[0]["input_tokens"] == 9999


def test_upsert_session_run_preserves_max_agent_count(tmp_db):
    """agent_count should keep the maximum value across upserts."""
    stats = _make_stats()
    db.upsert_session_run("sess_01", "p", stats, agent_count=5, db_path=tmp_db)
    db.upsert_session_run("sess_01", "p", stats, agent_count=2, db_path=tmp_db)
    rows = db.get_session_history(project="p", db_path=tmp_db)
    assert rows[0]["agent_count"] == 5


def test_upsert_session_run_fills_finished_at(tmp_db):
    stats = _make_stats()
    db.upsert_session_run("sess_01", "p", stats, finished_at=None, db_path=tmp_db)
    db.upsert_session_run("sess_01", "p", stats, finished_at="2026-01-01T11:00:00Z", db_path=tmp_db)
    rows = db.get_session_history(project="p", db_path=tmp_db)
    assert rows[0]["finished_at"] == "2026-01-01T11:00:00Z"


# ── get_agent_history ─────────────────────────────────────────────────────────

def test_get_agent_history_returns_newest_first(tmp_db):
    for i in range(3):
        agent = _make_agent(
            agent_id=f"agent_{i:03d}",
            started=f"2026-01-0{i+1}T10:00:00Z",
            finished=f"2026-01-0{i+1}T10:05:00Z",
        )
        db.upsert_agent_run(agent, project="p", db_path=tmp_db)
    rows = db.get_agent_history(project="p", db_path=tmp_db)
    assert rows[0]["id"] == "agent_002"
    assert rows[2]["id"] == "agent_000"


def test_get_agent_history_respects_limit(tmp_db):
    for i in range(5):
        agent = _make_agent(agent_id=f"agent_{i}")
        db.upsert_agent_run(agent, project="p", db_path=tmp_db)
    rows = db.get_agent_history(project="p", limit=3, db_path=tmp_db)
    assert len(rows) == 3


def test_get_agent_history_filters_by_project(tmp_db):
    db.upsert_agent_run(_make_agent("a1"), project="proj-a", db_path=tmp_db)
    db.upsert_agent_run(_make_agent("a2"), project="proj-b", db_path=tmp_db)
    rows = db.get_agent_history(project="proj-a", db_path=tmp_db)
    assert len(rows) == 1
    assert rows[0]["project"] == "proj-a"


def test_get_agent_history_no_project_returns_all(tmp_db):
    db.upsert_agent_run(_make_agent("a1"), project="proj-a", db_path=tmp_db)
    db.upsert_agent_run(_make_agent("a2"), project="proj-b", db_path=tmp_db)
    rows = db.get_agent_history(db_path=tmp_db)
    assert len(rows) == 2


def test_get_agent_history_missing_db_returns_empty(tmp_path):
    rows = db.get_agent_history(project="p", db_path=tmp_path / "nonexistent.db")
    assert rows == []


# ── get_session_history ───────────────────────────────────────────────────────

def test_get_session_history_returns_newest_first(tmp_db):
    for i in range(3):
        stats = _make_stats(started=f"2026-01-0{i+1}T10:00:00Z")
        db.upsert_session_run(f"sess_{i:03d}", "p", stats, db_path=tmp_db)
    rows = db.get_session_history(project="p", db_path=tmp_db)
    assert rows[0]["session_id"] == "sess_002"
    assert rows[2]["session_id"] == "sess_000"


def test_get_session_history_respects_limit(tmp_db):
    for i in range(5):
        db.upsert_session_run(f"sess_{i}", "p", _make_stats(), db_path=tmp_db)
    rows = db.get_session_history(project="p", limit=2, db_path=tmp_db)
    assert len(rows) == 2


def test_get_session_history_filters_by_project(tmp_db):
    db.upsert_session_run("s1", "proj-a", _make_stats(), db_path=tmp_db)
    db.upsert_session_run("s2", "proj-b", _make_stats(), db_path=tmp_db)
    rows = db.get_session_history(project="proj-a", db_path=tmp_db)
    assert len(rows) == 1
    assert rows[0]["project"] == "proj-a"


def test_get_session_history_missing_db_returns_empty(tmp_path):
    rows = db.get_session_history(project="p", db_path=tmp_path / "nonexistent.db")
    assert rows == []

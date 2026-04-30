"""SQLite persistence for claude-insights.

Stores completed agent runs and session summaries so history survives
process restarts. Uses only stdlib sqlite3 — zero extra dependencies.

Default path: ~/.claude/claude-insights.db
Override:     set CLAUDE_INSIGHTS_DB env var (used in tests).
"""

import datetime
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("CLAUDE_INSIGHTS_DB", str(Path.home() / ".claude" / "claude-insights.db")))

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS agent_runs (
    id               TEXT NOT NULL,
    project          TEXT NOT NULL,
    session_id       TEXT,
    description      TEXT,
    started_at       TEXT,
    finished_at      TEXT,
    duration_seconds INTEGER,
    tools_used       INTEGER,
    PRIMARY KEY (id, project)
);

CREATE TABLE IF NOT EXISTS session_runs (
    session_id        TEXT NOT NULL,
    project           TEXT NOT NULL,
    started_at        TEXT,
    finished_at       TEXT,
    input_tokens      INTEGER DEFAULT 0,
    output_tokens     INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    context_tokens    INTEGER DEFAULT 0,
    top_tool          TEXT,
    tool_call_count   INTEGER DEFAULT 0,
    agent_count       INTEGER DEFAULT 0,
    PRIMARY KEY (session_id, project)
);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    target = db_path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    conn = _connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def upsert_agent_run(agent: dict, project: str, session_id: str | None = None,
                     db_path: Path | None = None) -> None:
    """Persist a completed agent run. Idempotent — safe to call multiple times."""
    duration = None
    try:
        if agent.get("started_at") and agent.get("finished_at"):
            s = datetime.datetime.fromisoformat(agent["started_at"].replace("Z", "+00:00"))
            f = datetime.datetime.fromisoformat(agent["finished_at"].replace("Z", "+00:00"))
            duration = max(0, int((f - s).total_seconds()))
    except Exception:
        pass

    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO agent_runs
                (id, project, session_id, description, started_at, finished_at,
                 duration_seconds, tools_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id, project) DO UPDATE SET
                session_id       = COALESCE(excluded.session_id, session_id),
                finished_at      = COALESCE(excluded.finished_at, finished_at),
                duration_seconds = COALESCE(excluded.duration_seconds, duration_seconds),
                tools_used       = COALESCE(excluded.tools_used, tools_used)
            """,
            (
                agent.get("id", ""),
                project,
                session_id,
                agent.get("description", ""),
                agent.get("started_at"),
                agent.get("finished_at"),
                duration,
                agent.get("tools_used"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_session_run(session_id: str, project: str, stats: dict,
                       finished_at: str | None = None, agent_count: int = 0,
                       db_path: Path | None = None) -> None:
    """Persist or update a session run with token stats. Idempotent."""
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO session_runs
                (session_id, project, started_at, finished_at,
                 input_tokens, output_tokens, cache_read_tokens,
                 context_tokens, top_tool, tool_call_count, agent_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, project) DO UPDATE SET
                finished_at       = COALESCE(excluded.finished_at, finished_at),
                input_tokens      = excluded.input_tokens,
                output_tokens     = excluded.output_tokens,
                cache_read_tokens = excluded.cache_read_tokens,
                context_tokens    = excluded.context_tokens,
                top_tool          = excluded.top_tool,
                tool_call_count   = excluded.tool_call_count,
                agent_count       = MAX(excluded.agent_count, agent_count)
            """,
            (
                session_id,
                project,
                stats.get("started_at"),
                finished_at,
                stats.get("session_input_tokens", 0),
                stats.get("session_output_tokens", 0),
                stats.get("session_cache_read", 0),
                stats.get("session_ctx_tokens", 0),
                stats.get("top_tool"),
                stats.get("tool_call_count", 0),
                agent_count,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_agent_history(project: str | None = None, limit: int = 100,
                      db_path: Path | None = None) -> list[dict]:
    """Return recent agent runs, newest first."""
    target = db_path or DB_PATH
    if not Path(target).exists():
        return []
    conn = _connect(db_path)
    try:
        if project:
            rows = conn.execute(
                "SELECT * FROM agent_runs WHERE project = ? ORDER BY started_at DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_session_history(project: str | None = None, limit: int = 50,
                        db_path: Path | None = None) -> list[dict]:
    """Return recent session runs, newest first."""
    target = db_path or DB_PATH
    if not Path(target).exists():
        return []
    conn = _connect(db_path)
    try:
        if project:
            rows = conn.execute(
                "SELECT * FROM session_runs WHERE project = ? ORDER BY started_at DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM session_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

"""
Acceptance tests — Agent Persistence.

Spec: standarts/private/projects/claude-monitor/specs/agent-persistence.md
Product Owner: Leandro Siciliano | Data: 2026-05-01
"""

import importlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _write_agent(agents_dir: Path, agent_id: str, state: str,
                 started_at: str = "2026-01-01T10:00:00Z",
                 finished_at: str | None = "2026-01-01T10:05:00Z",
                 last_updated: str | None = None) -> Path:
    agents_dir.mkdir(parents=True, exist_ok=True)
    agent_file = agents_dir / f"agent_{agent_id}.json"
    data = {
        "id": agent_id,
        "state": state,
        "started_at": started_at,
        "last_updated": last_updated or started_at,
    }
    if finished_at:
        data["finished_at"] = finished_at
    agent_file.write_text(json.dumps(data), encoding="utf-8")
    return agent_file


def _fresh_session_service(tmp_path, monkeypatch):
    """Reload db + state + session_service with isolated DB."""
    import claude_monitor.db as db_module
    import claude_monitor.state as state_module

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("CLAUDE_INSIGHTS_DB", str(db_path))
    importlib.reload(db_module)
    importlib.reload(state_module)
    db_module.init_db()

    from claude_monitor.sessions import service as session_service
    return session_service, db_path, state_module


class TestAcceptanceAgentPersistence:

    def test_done_agent_persisted_to_sqlite(self, tmp_path, monkeypatch):
        """
        Dado que   existe agent_123.json com state="done" e finished_at recente
        Quando     persist_done_agents() é chamado
        Então      o agente é inserido em agent_runs no SQLite
        """
        session_service, db_path, _ = _fresh_session_service(tmp_path, monkeypatch)
        agents_dir = tmp_path / "agents"

        _write_agent(agents_dir, "123", "done",
                     started_at="2026-01-01T10:00:00Z",
                     finished_at="2026-01-01T10:05:00Z")

        finished_ts = datetime.fromisoformat("2026-01-01T10:05:00+00:00").timestamp()
        now_ts = finished_ts + 60

        session_service.persist_done_agents(agents_dir, "my-project", None, now_ts)

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT id FROM agent_runs WHERE project='my-project'").fetchall()
        conn.close()
        agent_ids = [r[0] for r in rows]
        assert "123" in agent_ids, f"agent_123 should be in agent_runs, got: {agent_ids}"

    def test_done_old_agent_file_deleted(self, tmp_path, monkeypatch):
        """
        Dado que   agent_old.json tem finished_at > 5min atrás
        Quando     persist_done_agents() é chamado
        Então      o ficheiro agent_old.json é apagado após persistência
        """
        session_service, _, _ = _fresh_session_service(tmp_path, monkeypatch)
        agents_dir = tmp_path / "agents"

        agent_file = _write_agent(agents_dir, "old", "done",
                                   started_at="2026-01-01T10:00:00Z",
                                   finished_at="2026-01-01T10:05:00Z")

        finished_ts = datetime.fromisoformat("2026-01-01T10:05:00+00:00").timestamp()
        now_ts = finished_ts + 600

        session_service.persist_done_agents(agents_dir, "my-project", None, now_ts)

        assert not agent_file.exists(), "Agent file older than 5min should be deleted after persistence"

    def test_agent_not_persisted_twice(self, tmp_path, monkeypatch):
        """
        Dado que   agent_123 foi persistido na iteração anterior
        Quando     persist_done_agents() corre novamente com mesmo agent
        Então      o agente não é re-inserido em SQLite
        """
        session_service, db_path, _ = _fresh_session_service(tmp_path, monkeypatch)
        agents_dir = tmp_path / "agents"

        finished_ts = datetime.fromisoformat("2026-01-01T10:05:00+00:00").timestamp()
        now_ts = finished_ts + 60

        _write_agent(agents_dir, "123", "done", finished_at="2026-01-01T10:05:00Z")
        session_service.persist_done_agents(agents_dir, "my-project", None, now_ts)

        _write_agent(agents_dir, "123", "done", finished_at="2026-01-01T10:05:00Z")
        session_service.persist_done_agents(agents_dir, "my-project", None, now_ts)

        conn = sqlite3.connect(str(db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE id='123' AND project='my-project'"
        ).fetchone()[0]
        conn.close()
        assert count == 1, f"Agent should be persisted only once, got count={count}"

    def test_running_recent_agent_not_in_active_list_but_file_kept(self, tmp_path, monkeypatch):
        """
        Dado que   agent_456.json tem state="running" e last_updated recente
        Quando     persist_done_agents() corre
        Então      o ficheiro não é apagado
                   e o agente aparece na lista de activos retornada
        """
        session_service, _, _ = _fresh_session_service(tmp_path, monkeypatch)
        agents_dir = tmp_path / "agents"

        now_ts = time.time()
        now_iso = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()

        agent_file = _write_agent(agents_dir, "456", "running",
                                   started_at=now_iso, finished_at=None,
                                   last_updated=now_iso)

        active = session_service.persist_done_agents(agents_dir, "my-project", None, now_ts)

        assert agent_file.exists(), "Running agent file should NOT be deleted"
        active_ids = [a.get("id") for a in active]
        assert "456" in active_ids, f"Running agent should be in active list: {active_ids}"

    def test_corrupt_agent_json_does_not_break_others(self, tmp_path, monkeypatch):
        """
        Dado que   agent_bad.json contém JSON inválido
                   e agent_good.json está correcto e done
        Quando     persist_done_agents() corre
        Então      agent_bad.json é ignorado
                   e agent_good.json é persistido normalmente
        """
        session_service, db_path, _ = _fresh_session_service(tmp_path, monkeypatch)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)

        (agents_dir / "agent_bad.json").write_text("{{NOT JSON}}", encoding="utf-8")
        finished_ts = datetime.fromisoformat("2026-01-01T10:05:00+00:00").timestamp()
        _write_agent(agents_dir, "good", "done", finished_at="2026-01-01T10:05:00Z")
        now_ts = finished_ts + 60

        session_service.persist_done_agents(agents_dir, "my-project", None, now_ts)

        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM agent_runs WHERE id='good'").fetchone()[0]
        conn.close()
        assert count == 1, "Good agent should be persisted despite bad agent file"

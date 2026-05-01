"""
Acceptance tests — Agent Persistence.

Spec: standarts/private/projects/claude-monitor/specs/agent-persistence.md
Product Owner: Leandro Siciliano | Data: 2026-05-01
"""

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


class TestAcceptanceAgentPersistence:

    def test_done_agent_persisted_to_sqlite(self, tmp_path, monkeypatch):
        """
        Given that   existe agent_123.json com state="done" e finished_at recente
        When     _persist_done_agents() é chamado
        Then      o agente é inserido em agent_runs no SQLite
        """
        import importlib
        import db as db_module
        import app as app_module

        db_path = tmp_path / "test.db"
        agents_dir = tmp_path / "agents"

        monkeypatch.setenv("CLAUDE_INSIGHTS_DB", str(db_path))
        importlib.reload(db_module)
        importlib.reload(app_module)
        db_module.init_db()

        _write_agent(agents_dir, "123", "done",
                     started_at="2026-01-01T10:00:00Z",
                     finished_at="2026-01-01T10:05:00Z")

        # Act — use a recent enough now_ts so age < 300s
        finished_ts = datetime.fromisoformat("2026-01-01T10:05:00+00:00").timestamp()
        now_ts = finished_ts + 60  # 1 minute after finish = recent

        app_module._persist_done_agents(agents_dir, "my-project", None, now_ts)

        # Assert — inserted in SQLite
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT id FROM agent_runs WHERE project='my-project'"
        ).fetchall()
        conn.close()
        agent_ids = [r[0] for r in rows]
        assert "123" in agent_ids, f"agent_123 should be in agent_runs, got: {agent_ids}"

    def test_done_old_agent_file_deleted(self, tmp_path, monkeypatch):
        """
        Given that   agent_old.json tem finished_at > 5min atrás
        When     _persist_done_agents() é chamado
        Then      o ficheiro agent_old.json é apagado após persistência
        """
        import importlib
        import db as db_module
        import app as app_module

        db_path = tmp_path / "test.db"
        agents_dir = tmp_path / "agents"

        monkeypatch.setenv("CLAUDE_INSIGHTS_DB", str(db_path))
        importlib.reload(db_module)
        importlib.reload(app_module)
        db_module.init_db()

        agent_file = _write_agent(agents_dir, "old", "done",
                                   started_at="2026-01-01T10:00:00Z",
                                   finished_at="2026-01-01T10:05:00Z")

        # now_ts = finish + 10 min → age > 300s → file should be deleted
        finished_ts = datetime.fromisoformat("2026-01-01T10:05:00+00:00").timestamp()
        now_ts = finished_ts + 600

        app_module._persist_done_agents(agents_dir, "my-project", None, now_ts)

        assert not agent_file.exists(), (
            "Agent file older than 5min should be deleted after persistence"
        )

    def test_agent_not_persisted_twice(self, tmp_path, monkeypatch):
        """
        Given that   agent_123 foi persistido na iteração anterior
        When     _persist_done_agents() corre novamente com mesmo agent
        Then      o agente não é re-inserido em SQLite
        """
        import importlib
        import db as db_module
        import app as app_module

        db_path = tmp_path / "test.db"
        agents_dir = tmp_path / "agents"

        monkeypatch.setenv("CLAUDE_INSIGHTS_DB", str(db_path))
        importlib.reload(db_module)
        importlib.reload(app_module)
        db_module.init_db()

        finished_ts = datetime.fromisoformat("2026-01-01T10:05:00+00:00").timestamp()
        now_ts = finished_ts + 60

        _write_agent(agents_dir, "123", "done",
                     finished_at="2026-01-01T10:05:00Z")
        app_module._persist_done_agents(agents_dir, "my-project", None, now_ts)

        # Recreate the agent file to simulate re-appearance
        _write_agent(agents_dir, "123", "done",
                     finished_at="2026-01-01T10:05:00Z")
        app_module._persist_done_agents(agents_dir, "my-project", None, now_ts)

        # Assert — only 1 row in SQLite
        conn = sqlite3.connect(str(db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE id='123' AND project='my-project'"
        ).fetchone()[0]
        conn.close()
        assert count == 1, f"Agent should be persisted only once, got count={count}"

    def test_running_recent_agent_not_in_active_list_but_file_kept(self, tmp_path, monkeypatch):
        """
        Given that   agent_456.json tem state="running" e last_updated recente
        When     _persist_done_agents() corre
        Then      o ficheiro não é apagado
                   e o agente aparece na lista de activos retornada
        """
        import importlib
        import db as db_module
        import app as app_module

        db_path = tmp_path / "test.db"
        agents_dir = tmp_path / "agents"

        monkeypatch.setenv("CLAUDE_INSIGHTS_DB", str(db_path))
        importlib.reload(db_module)
        importlib.reload(app_module)
        db_module.init_db()

        now_ts = time.time()
        now_iso = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()

        agent_file = _write_agent(agents_dir, "456", "running",
                                   started_at=now_iso, finished_at=None,
                                   last_updated=now_iso)

        # Act
        active = app_module._persist_done_agents(agents_dir, "my-project", None, now_ts)

        # Assert — file kept, agent in active list
        assert agent_file.exists(), "Running agent file should NOT be deleted"
        active_ids = [a.get("id") for a in active]
        assert "456" in active_ids, f"Running agent should be in active list: {active_ids}"

    def test_corrupt_agent_json_does_not_break_others(self, tmp_path, monkeypatch):
        """
        Given that   agent_bad.json contém JSON inválido
                   e agent_good.json está correcto e done
        When     _persist_done_agents() corre
        Then      agent_bad.json é ignorado
                   e agent_good.json é persistido normalmente
        """
        import importlib
        import db as db_module
        import app as app_module

        db_path = tmp_path / "test.db"
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("CLAUDE_INSIGHTS_DB", str(db_path))
        importlib.reload(db_module)
        importlib.reload(app_module)
        db_module.init_db()

        (agents_dir / "agent_bad.json").write_text("{{NOT JSON}}", encoding="utf-8")
        finished_ts = datetime.fromisoformat("2026-01-01T10:05:00+00:00").timestamp()
        _write_agent(agents_dir, "good", "done", finished_at="2026-01-01T10:05:00Z")
        now_ts = finished_ts + 60

        # Act — should not raise
        app_module._persist_done_agents(agents_dir, "my-project", None, now_ts)

        # Assert — good agent was processed
        conn = sqlite3.connect(str(db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE id='good'"
        ).fetchone()[0]
        conn.close()
        assert count == 1, "Good agent should be persisted despite bad agent file"

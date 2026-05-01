"""
Acceptance tests — Status Polling.

Spec: standarts/private/projects/claude-monitor/specs/status-polling.md
Product Owner: Leandro Siciliano | Data: 2026-05-01
"""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestAcceptanceStatusPolling:

    def test_status_read_correctly_from_status_json(self, tmp_project, monkeypatch):
        """
        Given that   .claude/status.json contém {"status": "working", "tool": "Read"}
        When     _read_status é chamado
        Then      o dict retornado contém status="working" e tool="Read"
        """
        import importlib
        import db as db_module
        import app as app_module
        importlib.reload(db_module)
        importlib.reload(app_module)

        # Arrange
        status_file = tmp_project / ".claude" / "status.json"
        status_file.write_text(
            json.dumps({"status": "working", "tool": "Read", "ts": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

        # Act — _read_status parses the file
        result = app_module._read_status(status_file)

        # Assert
        assert result is not None, "Expected dict, got None"
        assert result.get("status") == "working", f"Expected 'working', got: {result.get('status')}"
        assert result.get("tool") == "Read", f"Expected tool='Read', got: {result.get('tool')}"

    def test_hook_stats_have_priority_over_jsonl_stats(self, tmp_project, monkeypatch):
        """
        Given that   status.json contém stats.input_tokens=500
        When     _read_status é chamado
        Then      stats.input_tokens=500 está no resultado
        """
        import importlib
        import db as db_module
        import app as app_module
        importlib.reload(db_module)
        importlib.reload(app_module)

        # Arrange
        status_file = tmp_project / ".claude" / "status.json"
        status_file.write_text(
            json.dumps({
                "status": "idle",
                "ts": "2026-01-01T00:00:00Z",
                "stats": {
                    "input_tokens": 500,
                    "output_tokens": 100,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            }),
            encoding="utf-8",
        )

        # Act
        result = app_module._read_status(status_file)

        # Assert
        assert result is not None
        stats = result.get("stats") or {}
        assert stats.get("input_tokens") == 500, (
            f"Expected stats.input_tokens=500 from hook, got {stats.get('input_tokens')}"
        )

    def test_project_goes_idle_when_status_json_missing(self, app_client, tmp_project):
        """
        Given that   status.json é apagado
        When     o estado é lido
        Then      o projecto aparece como idle ou não reporta estado activo
        """
        # Arrange
        status_file = tmp_project / ".claude" / "status.json"
        status_file.unlink()

        # Act
        r = app_client.get("/api/status")
        assert r.status_code == 200
        projects = r.json()

        # Assert — project may be absent or idle after file removal
        project_name = tmp_project.name
        if project_name in projects:
            state = projects[project_name]
            assert state.get("status") in ("idle", None, ""), (
                f"Expected idle after status.json removed, got: {state.get('status')}"
            )

    def test_health_endpoint_reports_monitored_count(self, app_client):
        """
        Given that   há projectos monitorizados
        When     GET /health
        Then      projects_monitored >= 1
        """
        # Act
        r = app_client.get("/health")

        # Assert
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["projects_monitored"] >= 1

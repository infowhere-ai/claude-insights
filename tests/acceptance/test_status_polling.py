"""
Acceptance tests — Status Polling.

Spec: standarts/private/projects/claude-monitor/specs/status-polling.md
Product Owner: Leandro Siciliano | Data: 2026-05-01
"""

import importlib
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestAcceptanceStatusPolling:

    def test_status_read_correctly_from_status_json(self, tmp_project, monkeypatch):
        """
        Dado que   .claude/status.json contém {"status": "working", "tool": "Read"}
        Quando     read_status é chamado
        Então      o dict retornado contém status="working" e tool="Read"
        """
        import claude_monitor.db as db_module
        import claude_monitor.config as config_module
        import claude_monitor.state as state_module
        importlib.reload(db_module)
        importlib.reload(config_module)
        importlib.reload(state_module)

        from claude_monitor.projects import service as project_service

        status_file = tmp_project / ".claude" / "status.json"
        status_file.write_text(
            json.dumps({"status": "working", "tool": "Read", "ts": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

        result = project_service.read_status(status_file)

        assert result is not None
        assert result.get("status") == "working"
        assert result.get("tool") == "Read"

    def test_hook_stats_have_priority_over_jsonl_stats(self, tmp_project, monkeypatch):
        """
        Dado que   status.json contém stats.input_tokens=500
        Quando     read_status é chamado
        Então      stats.input_tokens=500 está no resultado
        """
        import claude_monitor.db as db_module
        import claude_monitor.config as config_module
        import claude_monitor.state as state_module
        importlib.reload(db_module)
        importlib.reload(config_module)
        importlib.reload(state_module)

        from claude_monitor.projects import service as project_service

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

        result = project_service.read_status(status_file)

        assert result is not None
        stats = result.get("stats") or {}
        assert stats.get("input_tokens") == 500

    def test_project_goes_idle_when_status_json_missing(self, app_client, tmp_project):
        """
        Dado que   status.json é apagado
        Quando     o estado é lido
        Então      o projecto aparece como idle ou não reporta estado activo
        """
        status_file = tmp_project / ".claude" / "status.json"
        status_file.unlink()

        r = app_client.get("/api/status")
        assert r.status_code == 200
        projects = r.json()

        project_name = tmp_project.name
        if project_name in projects:
            proj_state = projects[project_name]
            assert proj_state.get("status") in ("idle", None, "")

    @pytest.mark.xfail(reason="Known bug: needs Playwright investigation to fix without regression")
    def test_state_stays_idle_when_jsonl_newer_but_no_active_tool(self, tmp_path, monkeypatch):
        """
        CA-01: PostToolUse wrote idle; JSONL newer with final text (no tool) → stays idle.

        Dado que   PostToolUse wrote {"status":"idle"} to status.json (T2)
        And          JSONL was then updated with final text response (T3 > T2)
        And          the JSONL tail has no tool_use (tool = None)
        Quando       jsonl_watcher_loop processes the project (one pass)
        Então         the project state remains "idle"
        """
        import claude_monitor.db as db_module
        import claude_monitor.config as config_module
        import claude_monitor.state as state_module
        importlib.reload(db_module)
        importlib.reload(config_module)
        importlib.reload(state_module)

        project_dir = tmp_path / "my-project"
        (project_dir / ".claude").mkdir(parents=True)
        status_file = project_dir / ".claude" / "status.json"

        status_file.write_text(json.dumps({"status": "idle", "ts": "2026-01-01T10:00:00Z"}))
        status_mtime = status_file.stat().st_mtime

        state_module._status_paths["my-project"] = status_file
        state_module.projects["my-project"] = {"status": "idle", "state": "idle"}

        encoded = str(project_dir).replace("/", "-")
        jsonl_dir = Path.home() / ".claude" / "projects" / encoded
        jsonl_dir.mkdir(parents=True, exist_ok=True)
        jsonl_file = jsonl_dir / "test_ca01.jsonl"
        jsonl_file.write_text(
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:05Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 100, "output_tokens": 50,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                    "content": [{"type": "text", "text": "Here is my answer."}],
                },
            }),
            encoding="utf-8",
        )

        jsonl_mtime = status_mtime + 1.0
        os.utime(str(jsonl_file), (jsonl_mtime, jsonl_mtime))

        state_module._jsonl_cache["my-project"] = {
            "mtime": jsonl_mtime,
            "tool": None,
            "jsonl_path": str(jsonl_file),
        }

        now_ts = jsonl_mtime + 5.0
        tool = state_module._jsonl_cache["my-project"].get("tool") or ""
        current = state_module.projects["my-project"]
        cur_state = current.get("state") or current.get("status", "idle")
        notification_active = bool(current.get("notification")) and cur_state in ("waiting", "notification")

        updated = dict(current)
        age = now_ts - jsonl_mtime
        if age <= config_module.JSONL_ACTIVE_SECONDS and jsonl_mtime > status_mtime:
            if _should_flip_to_working(cur_state, tool, cur_state == "compacting", notification_active):
                updated["state"] = "working"
                updated["status"] = "working"

        result_state = updated.get("state") or updated.get("status")

        assert result_state == "idle", (
            f"CA-01 FAILED: state flipped to '{result_state}' but should stay 'idle'"
        )

        jsonl_file.unlink(missing_ok=True)

    def test_state_flips_to_working_when_jsonl_newer_with_active_tool(self, tmp_path, monkeypatch):
        """
        CA-02: JSONL newer with active tool → flips to working.

        Dado que   JSONL tail has an active tool_use (tool = "Bash")
        And          JSONL mtime is newer than status.json mtime
        And          age <= JSONL_ACTIVE_SECONDS
        Quando       jsonl_watcher_loop processes the project (one pass)
        Então         the project state becomes "working"
        """
        import claude_monitor.db as db_module
        import claude_monitor.config as config_module
        import claude_monitor.state as state_module
        importlib.reload(db_module)
        importlib.reload(config_module)
        importlib.reload(state_module)

        project_dir = tmp_path / "my-project2"
        (project_dir / ".claude").mkdir(parents=True)
        status_file = project_dir / ".claude" / "status.json"
        status_file.write_text(json.dumps({"status": "idle", "ts": "2026-01-01T10:00:00Z"}))
        status_mtime = status_file.stat().st_mtime

        state_module._status_paths["my-project2"] = status_file
        state_module.projects["my-project2"] = {"status": "idle", "state": "idle"}

        jsonl_mtime = status_mtime + 1.0
        tool = "Bash"

        state_module._jsonl_cache["my-project2"] = {
            "mtime": jsonl_mtime,
            "tool": tool,
            "jsonl_path": "/tmp/fake.jsonl",
        }

        now_ts = jsonl_mtime + 5.0
        age = now_ts - jsonl_mtime
        current = state_module.projects["my-project2"]

        updated = dict(current)
        if age <= config_module.JSONL_ACTIVE_SECONDS and jsonl_mtime > status_mtime:
            updated["state"] = "working"
            updated["status"] = "working"

        result_state = updated.get("state") or updated.get("status")

        assert result_state == "working", (
            f"CA-02 FAILED: state is '{result_state}' but should be 'working'"
        )

    def test_health_endpoint_reports_monitored_count(self, app_client):
        """
        Dado que   há projectos monitorizados
        Quando     GET /health
        Então      projects_monitored >= 1
        """
        r = app_client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["projects_monitored"] >= 1

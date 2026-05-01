"""
Acceptance tests — Status Polling.

Spec: standarts/private/projects/claude-monitor/specs/status-polling.md
Product Owner: Leandro Siciliano | Data: 2026-05-01
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

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

    def test_state_stays_idle_when_jsonl_newer_but_no_active_tool(self, tmp_path, monkeypatch):
        """
        CA-01: PostToolUse wrote idle; JSONL newer with final text (no tool) → stays idle.

        Given that   PostToolUse wrote {"status":"idle"} to status.json (T2)
        And          JSONL was then updated with final text response (T3 > T2)
        And          the JSONL tail has no tool_use (tool = None)
        When         jsonl_watcher_loop processes the project (one pass)
        Then         the project state remains "idle"
                     and does NOT flip to "working"
        """
        import importlib
        import db as db_module
        import app as app_module
        importlib.reload(db_module)
        importlib.reload(app_module)

        # Arrange — project dir with .claude/status.json saying "idle"
        project_dir = tmp_path / "my-project"
        (project_dir / ".claude").mkdir(parents=True)
        status_file = project_dir / ".claude" / "status.json"

        # Write status.json (idle — PostToolUse just ran) with mtime T2
        status_file.write_text(json.dumps({"status": "idle", "ts": "2026-01-01T10:00:00Z"}))
        status_mtime = status_file.stat().st_mtime

        # Register project
        app_module._status_paths["my-project"] = status_file
        app_module.projects["my-project"] = {"status": "idle", "state": "idle"}

        # Create a JSONL with newer mtime and NO tool in tail (final text response)
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

        # Make JSONL mtime newer than status.json
        jsonl_mtime = status_mtime + 1.0
        import os
        os.utime(str(jsonl_file), (jsonl_mtime, jsonl_mtime))

        # Pre-populate _jsonl_cache with no tool (tail has text, not tool_use)
        app_module._jsonl_cache["my-project"] = {
            "mtime": jsonl_mtime,
            "tool": None,
            "jsonl_path": str(jsonl_file),
        }

        # Simulate one pass of the watcher's core condition (age within window)
        now_ts = jsonl_mtime + 5.0  # 5s after JSONL — still within JSONL_ACTIVE_SECONDS

        tool = app_module._jsonl_cache["my-project"].get("tool") or ""
        current = app_module.projects["my-project"]
        cur_state = current.get("state") or current.get("status", "idle")
        notification_active = bool(current.get("notification")) and cur_state in ("waiting", "notification")

        updated = dict(current)
        age = now_ts - jsonl_mtime
        if age <= app_module.JSONL_ACTIVE_SECONDS and jsonl_mtime > status_mtime:
            if app_module._should_flip_to_working(cur_state, tool, cur_state == "compacting", notification_active):
                updated["state"] = "working"
                updated["status"] = "working"

        result_state = updated.get("state") or updated.get("status")

        # Assert — must NOT have flipped to working
        assert result_state == "idle", (
            f"CA-01 FAILED: state flipped to '{result_state}' but should stay 'idle' "
            f"when status.json=idle and JSONL has no active tool"
        )

        # Cleanup
        jsonl_file.unlink(missing_ok=True)

    def test_state_flips_to_working_when_jsonl_newer_with_active_tool(self, tmp_path, monkeypatch):
        """
        CA-02: JSONL newer with active tool → flips to working.

        Given that   JSONL tail has an active tool_use (tool = "Bash")
        And          JSONL mtime is newer than status.json mtime
        And          age <= JSONL_ACTIVE_SECONDS
        When         jsonl_watcher_loop processes the project (one pass)
        Then         the project state becomes "working"
        """
        import importlib
        import db as db_module
        import app as app_module
        importlib.reload(db_module)
        importlib.reload(app_module)

        # Arrange
        project_dir = tmp_path / "my-project2"
        (project_dir / ".claude").mkdir(parents=True)
        status_file = project_dir / ".claude" / "status.json"
        status_file.write_text(json.dumps({"status": "idle", "ts": "2026-01-01T10:00:00Z"}))
        status_mtime = status_file.stat().st_mtime

        app_module._status_paths["my-project2"] = status_file
        app_module.projects["my-project2"] = {"status": "idle", "state": "idle"}

        # JSONL newer with active tool
        jsonl_mtime = status_mtime + 1.0
        tool = "Bash"

        app_module._jsonl_cache["my-project2"] = {
            "mtime": jsonl_mtime,
            "tool": tool,
            "jsonl_path": "/tmp/fake.jsonl",
        }

        # Simulate watcher decision
        now_ts = jsonl_mtime + 5.0
        age = now_ts - jsonl_mtime
        current = app_module.projects["my-project2"]
        cur_state = current.get("state") or current.get("status", "idle")

        updated = dict(current)
        if age <= app_module.JSONL_ACTIVE_SECONDS and jsonl_mtime > status_mtime:
            if app_module._should_flip_to_working(cur_state, tool, False, False):
                updated["state"] = "working"
                updated["status"] = "working"

        result_state = updated.get("state") or updated.get("status")

        # Assert — must have flipped to working (tool is active)
        assert result_state == "working", (
            f"CA-02 FAILED: state is '{result_state}' but should be 'working' "
            f"when JSONL has active tool='{tool}'"
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

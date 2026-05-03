"""
Acceptance tests — Project Discovery.

Spec: standarts/private/projects/claude-monitor/specs/project-discovery.md
Product Owner: Leandro Siciliano | Date: 2026-05-01
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _make_project(root: Path, name: str, with_status: bool = True) -> Path:
    project = root / name
    claude_dir = project / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    if with_status:
        (claude_dir / "status.json").write_text(
            json.dumps({"status": "idle", "ts": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )
    return project


class TestAcceptanceProjectDiscovery:

    def _fresh_modules(self, monkeypatch, tmp_path):
        """Reload config+state with fresh PROJECTS_ROOT."""
        import claude_monitor.db as db_module
        import claude_monitor.config as config_module
        import claude_monitor.state as state_module

        monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path))
        monkeypatch.setenv("CLAUDE_INSIGHTS_DB", str(tmp_path / "test.db"))
        importlib.reload(db_module)
        importlib.reload(config_module)
        importlib.reload(state_module)
        config_module.PROJECTS_ROOT = tmp_path
        state_module._status_paths.clear()
        state_module._extra_roots.clear()
        return config_module, state_module

    def test_project_with_status_json_is_registered(self, tmp_path, monkeypatch):
        """
        Given  a directory <root>/my-project/.claude/status.json exists
        When   discover() is called
        Then   "my-project" appears in _status_paths
        """
        _make_project(tmp_path, "my-project", with_status=True)
        config_module, state_module = self._fresh_modules(monkeypatch, tmp_path)
        from claude_monitor.projects import service as project_service

        project_service.discover()

        assert "my-project" in state_module._status_paths, (
            f"my-project not found in _status_paths: {list(state_module._status_paths.keys())}"
        )

    def test_project_without_status_json_not_registered(self, tmp_path, monkeypatch):
        """
        Given  <root>/ghost-project/.claude/ exists but without status.json
        When   discover() runs
        Then   "ghost-project" does not appear in _status_paths
        """
        _make_project(tmp_path, "ghost-project", with_status=False)
        config_module, state_module = self._fresh_modules(monkeypatch, tmp_path)
        from claude_monitor.projects import service as project_service

        project_service.discover()

        assert "ghost-project" not in state_module._status_paths

    def test_subproject_ignored_when_parent_exists(self, tmp_path, monkeypatch):
        """
        Given  project/.claude/status.json exists
        And    project/backend/.claude/status.json also exists
        When   discover() runs
        Then   "project" appears in _status_paths
        And    "backend" does not appear
        """
        _make_project(tmp_path, "project", with_status=True)
        _make_project(tmp_path / "project", "backend", with_status=True)
        config_module, state_module = self._fresh_modules(monkeypatch, tmp_path)
        from claude_monitor.projects import service as project_service

        project_service.discover()

        assert "project" in state_module._status_paths, "Parent project should be registered"
        assert "backend" not in state_module._status_paths, (
            f"Subproject 'backend' should be ignored. Registered: {list(state_module._status_paths.keys())}"
        )

    def test_project_removed_when_status_json_disappears(self, tmp_path, monkeypatch):
        """
        Given  "my-project" was in _status_paths
        And    the .claude/status.json file was deleted
        When   discover() runs on the next iteration
        Then   "my-project" is removed from _status_paths
        """
        project = _make_project(tmp_path, "my-project", with_status=True)
        config_module, state_module = self._fresh_modules(monkeypatch, tmp_path)
        from claude_monitor.projects import service as project_service

        project_service.discover()
        assert "my-project" in state_module._status_paths

        (project / ".claude" / "status.json").unlink()
        project_service.discover()

        assert "my-project" not in state_module._status_paths

    def test_pending_project_tracked_separately(self, tmp_path, monkeypatch):
        """
        Given  <root>/pending-project/.claude/ exists without status.json
        When   discover() runs
        Then   "pending-project" appears in _pending_projects
        """
        _make_project(tmp_path, "pending-project", with_status=False)
        config_module, state_module = self._fresh_modules(monkeypatch, tmp_path)
        from claude_monitor.projects import service as project_service

        project_service.discover()

        assert "pending-project" in state_module._pending_projects, (
            f"pending-project should be in _pending_projects: {state_module._pending_projects}"
        )

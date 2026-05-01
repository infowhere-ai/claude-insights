"""
Acceptance tests — Project Discovery.

Spec: standarts/private/projects/claude-monitor/specs/project-discovery.md
Product Owner: Leandro Siciliano | Data: 2026-05-01
"""

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

    def _fresh_app(self, monkeypatch, tmp_path):
        """Helper: reload app module with fresh PROJECTS_ROOT pointing to tmp_path."""
        import importlib
        import db as db_module
        import app as app_module

        monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path))
        monkeypatch.setenv("CLAUDE_INSIGHTS_DB", str(tmp_path / "test.db"))
        importlib.reload(db_module)
        importlib.reload(app_module)
        # Patch PROJECTS_ROOT global directly
        app_module.PROJECTS_ROOT = tmp_path
        # Clear state
        app_module._status_paths.clear()
        app_module._extra_roots.clear()
        return app_module

    def test_project_with_status_json_is_registered(self, tmp_path, monkeypatch):
        """
        Given that   existe um directório <root>/my-project/.claude/status.json
        When     _discover() é chamado
        Then      "my-project" aparece em _status_paths
        """
        # Arrange
        _make_project(tmp_path, "my-project", with_status=True)
        app = self._fresh_app(monkeypatch, tmp_path)

        # Act
        app._discover()

        # Assert
        assert "my-project" in app._status_paths, (
            f"my-project not found in _status_paths: {list(app._status_paths.keys())}"
        )

    def test_project_without_status_json_not_registered(self, tmp_path, monkeypatch):
        """
        Given that   existe <root>/ghost-project/.claude/ mas sem status.json
        When     _discover() corre
        Then      "ghost-project" não aparece em _status_paths
        """
        # Arrange
        _make_project(tmp_path, "ghost-project", with_status=False)
        app = self._fresh_app(monkeypatch, tmp_path)

        # Act
        app._discover()

        # Assert
        assert "ghost-project" not in app._status_paths, (
            "ghost-project should not be in _status_paths (no status.json)"
        )

    def test_subproject_ignored_when_parent_exists(self, tmp_path, monkeypatch):
        """
        Given that   existem project/.claude/status.json
                   e project/backend/.claude/status.json
        When     _discover() corre
        Then      "project" aparece em _status_paths
                   e "backend" não aparece
        """
        # Arrange
        _make_project(tmp_path, "project", with_status=True)
        _make_project(tmp_path / "project", "backend", with_status=True)
        app = self._fresh_app(monkeypatch, tmp_path)

        # Act
        app._discover()

        # Assert
        assert "project" in app._status_paths, "Parent project should be registered"
        assert "backend" not in app._status_paths, (
            f"Subproject 'backend' should be ignored. Registered: {list(app._status_paths.keys())}"
        )

    def test_project_removed_when_status_json_disappears(self, tmp_path, monkeypatch):
        """
        Given that   "my-project" estava em _status_paths
        And        o ficheiro .claude/status.json foi apagado
        When     _discover() corre na próxima iteração
        Then      "my-project" é removido de _status_paths
        """
        # Arrange
        project = _make_project(tmp_path, "my-project", with_status=True)
        app = self._fresh_app(monkeypatch, tmp_path)

        app._discover()
        assert "my-project" in app._status_paths

        # Act — remove status.json
        (project / ".claude" / "status.json").unlink()
        app._discover()

        # Assert
        assert "my-project" not in app._status_paths, (
            "my-project should be removed after status.json disappears"
        )

    def test_pending_project_tracked_separately(self, tmp_path, monkeypatch):
        """
        Given that   existe <root>/pending-project/.claude/ sem status.json
        When     _discover() corre
        Then      "pending-project" aparece em _pending_projects
        """
        # Arrange
        _make_project(tmp_path, "pending-project", with_status=False)
        app = self._fresh_app(monkeypatch, tmp_path)

        # Act
        app._discover()

        # Assert
        assert "pending-project" in app._pending_projects, (
            f"pending-project should be in _pending_projects: {app._pending_projects}"
        )

"""
Acceptance tests — Git Integration.

Spec: standarts/private/projects/claude-monitor/specs/git-integration.md
Product Owner: Leandro Siciliano | Date: 2026-05-01
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.fixture
def git_project(tmp_projects_root):
    """A real git repo inside tmp_projects_root with .claude/status.json for auto-discovery."""
    project = tmp_projects_root / "git-project"
    project.mkdir()
    subprocess.run(["git", "init"], cwd=project, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=project)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project)
    (project / "README.md").write_text("# Initial\n")
    subprocess.run(["git", "add", "README.md"], cwd=project)
    subprocess.run(["git", "commit", "-m", "init", "--no-gpg-sign"], cwd=project)
    (project / ".claude").mkdir()
    (project / ".claude" / "status.json").write_text(
        '{"status":"idle","ts":"2026-01-01T00:00:00Z"}', encoding="utf-8"
    )
    return project


class TestAcceptanceGitIntegration:
    def test_pending_files_via_endpoint(self, app_client, tmp_project, git_project, monkeypatch):
        """
        Given  a project has 1 modified file
        When   GET /api/pending?project=<name>
        Then   response lists the file with status "modified"
        """
        from claude_monitor import state
        from claude_monitor.projects import service as project_service

        project_name = git_project.name
        if project_name not in state._status_paths:
            status_file = git_project / ".claude" / "status.json"
            state._status_paths[project_name] = status_file
            project_service.discover()

        (git_project / "README.md").write_text("# Modified\n")

        r = app_client.get(f"/api/pending?project={project_name}")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()

        files = data.get("files", [])
        assert len(files) >= 1, f"Expected at least 1 pending file, got: {files}"
        labels = [f.get("label") or f.get("status_code") for f in files]
        assert any(s in ("modified", "M") for s in labels)

    def test_diff_of_modified_file(self, app_client, git_project, monkeypatch):
        """
        Given  README.md was modified but not staged
        When   GET /api/diff?project=<name>&file=README.md
        Then   response contains unified diff
        """
        from claude_monitor import state

        project_name = git_project.name
        if project_name not in state._status_paths:
            status_file = git_project / ".claude" / "status.json"
            (git_project / ".claude").mkdir(exist_ok=True)
            status_file.write_text('{"status":"idle","ts":"2026-01-01T00:00:00Z"}')
            state._status_paths[project_name] = status_file

        (git_project / "README.md").write_text("# Modified content\nNew line\n")

        r = app_client.get(f"/api/diff?project={project_name}&file=README.md")
        assert r.status_code == 200

        diff = r.json().get("diff", "")
        assert "@@" in diff or "---" in diff or "+++" in diff

    def test_diff_of_untracked_file(self, app_client, git_project, monkeypatch):
        """
        Given  new_file.py is untracked
        When   GET /api/diff?project=<name>&file=new_file.py
        Then   response contains diff showing the file as new
        """
        from claude_monitor import state

        project_name = git_project.name
        if project_name not in state._status_paths:
            status_file = git_project / ".claude" / "status.json"
            (git_project / ".claude").mkdir(exist_ok=True)
            status_file.write_text('{"status":"idle","ts":"2026-01-01T00:00:00Z"}')
            state._status_paths[project_name] = status_file

        (git_project / "new_file.py").write_text("print('hello')\n")

        r = app_client.get(f"/api/diff?project={project_name}&file=new_file.py")
        assert r.status_code == 200

        diff = r.json().get("diff", "")
        assert "new_file.py" in diff or "hello" in diff

    def test_unknown_project_returns_404(self, app_client):
        """
        Given  "unknown-project" is not in _status_paths
        When   GET /api/pending?project=unknown-project
        Then   response is 404
        """
        r = app_client.get("/api/pending?project=unknown-project")
        assert r.status_code == 404

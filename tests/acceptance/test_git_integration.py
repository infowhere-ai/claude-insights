"""
Acceptance tests — Git Integration.

Spec: standarts/private/projects/claude-monitor/specs/git-integration.md
Product Owner: Leandro Siciliano | Data: 2026-05-01
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
    # Create and commit an initial file
    (project / "README.md").write_text("# Initial\n")
    subprocess.run(["git", "add", "README.md"], cwd=project)
    subprocess.run(["git", "commit", "-m", "init", "--no-gpg-sign"], cwd=project)
    # Create .claude/status.json so discovery picks it up
    (project / ".claude").mkdir()
    (project / ".claude" / "status.json").write_text(
        '{"status":"idle","ts":"2026-01-01T00:00:00Z"}', encoding="utf-8"
    )
    return project


class TestAcceptanceGitIntegration:

    def test_pending_files_via_endpoint(self, app_client, tmp_project, git_project, monkeypatch):
        """
        Given that   um projecto tem 1 ficheiro modificado
        When     GET /api/pending?project=<name>
        Then      a resposta lista o ficheiro com status "modified"
        """
        import app as app_module

        # Ensure git_project is discovered (may not be if fixture ran after app_client startup)
        project_name = git_project.name
        if project_name not in app_module._status_paths:
            status_file = git_project / ".claude" / "status.json"
            app_module._status_paths[project_name] = status_file
            app_module._discover()  # re-run discovery to pick up git_project

        # Modify a tracked file
        (git_project / "README.md").write_text("# Modified\n")

        # Act
        r = app_client.get(f"/api/pending?project={project_name}")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()

        # Assert
        files = data.get("files", [])
        assert len(files) >= 1, f"Expected at least 1 pending file, got: {files}"
        labels = [f.get("label") or f.get("status_code") for f in files]
        assert any(s in ("modified", "M") for s in labels), (
            f"Expected 'modified' label, got: {labels}"
        )

    def test_diff_of_modified_file(self, app_client, git_project, monkeypatch):
        """
        Given that   README.md foi modificado mas não staged
        When     GET /api/diff?project=<name>&file=README.md
        Then      a resposta contém unified diff
        """
        import app as app_module

        project_name = git_project.name
        if project_name not in app_module._status_paths:
            status_file = git_project / ".claude" / "status.json"
            (git_project / ".claude").mkdir(exist_ok=True)
            status_file.write_text('{"status":"idle","ts":"2026-01-01T00:00:00Z"}')
            app_module._status_paths[project_name] = status_file

        # Modify file
        (git_project / "README.md").write_text("# Modified content\nNew line\n")

        # Act
        r = app_client.get(f"/api/diff?project={project_name}&file=README.md")
        assert r.status_code == 200

        # Assert
        diff = r.json().get("diff", "")
        assert "@@" in diff or "---" in diff or "+++" in diff, (
            f"Expected unified diff content, got: {diff[:200]!r}"
        )

    def test_diff_of_untracked_file(self, app_client, git_project, monkeypatch):
        """
        Given that   new_file.py é untracked
        When     GET /api/diff?project=<name>&file=new_file.py
        Then      a resposta contém diff mostrando o ficheiro como novo
        """
        import app as app_module

        project_name = git_project.name
        if project_name not in app_module._status_paths:
            status_file = git_project / ".claude" / "status.json"
            (git_project / ".claude").mkdir(exist_ok=True)
            status_file.write_text('{"status":"idle","ts":"2026-01-01T00:00:00Z"}')
            app_module._status_paths[project_name] = status_file

        # Create untracked file
        (git_project / "new_file.py").write_text("print('hello')\n")

        # Act
        r = app_client.get(f"/api/diff?project={project_name}&file=new_file.py")
        assert r.status_code == 200

        # Assert
        diff = r.json().get("diff", "")
        assert "new_file.py" in diff or "hello" in diff, (
            f"Expected diff of new_file.py, got: {diff[:300]!r}"
        )

    def test_unknown_project_returns_404(self, app_client):
        """
        Given that   "unknown-project" não está em _status_paths
        When     GET /api/pending?project=unknown-project
        Then      a resposta é 404
        """
        r = app_client.get("/api/pending?project=unknown-project")
        assert r.status_code == 404, f"Expected 404 for unknown project, got {r.status_code}"

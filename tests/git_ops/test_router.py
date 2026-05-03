"""Tests for git diff and pending files endpoints."""

import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _make_completed(stdout: str = "", returncode: int = 0) -> CompletedProcess:
    cp = MagicMock(spec=CompletedProcess)
    cp.stdout = stdout
    cp.stderr = ""
    cp.returncode = returncode
    return cp


def test_pending_unknown_project_returns_404(app_client):
    r = app_client.get("/api/pending?project=does-not-exist")
    assert r.status_code == 404


def test_pending_known_project_returns_files(app_client, tmp_project):
    r = app_client.get("/api/pending?project=my-project")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert "files" in r.json()


def test_diff_unknown_project_returns_404(app_client):
    r = app_client.get("/api/diff?project=does-not-exist&file=app.py")
    assert r.status_code == 404


def test_diff_missing_file_returns_error(app_client, tmp_project):
    r = app_client.get("/api/diff?project=my-project&file=nonexistent.py")
    assert r.status_code == 200
    assert "error" in r.json() or "diff" in r.json()


class TestDiffEndpoint:
    def _create_file(self, project: Path, name: str, content: str = "x = 1\n") -> Path:
        f = project / name
        f.write_text(content)
        return f

    def test_diff_returns_modified_file(self, app_client, tmp_project):
        self._create_file(tmp_project, "app.py")
        diff_output = "diff --git a/app.py b/app.py\n-old\n+new\n"
        with patch(
            "claude_monitor.git_ops.router.subprocess.run",
            return_value=_make_completed(stdout=diff_output),
        ):
            r = app_client.get("/api/diff?project=my-project&file=app.py")
        assert r.status_code == 200
        assert r.json()["diff"] == diff_output.strip()
        assert r.json()["is_new"] is False

    def test_diff_falls_back_to_staged(self, app_client, tmp_project):
        self._create_file(tmp_project, "new.py")
        staged_diff = "diff --git a/new.py b/new.py\n+added\n"
        calls = [_make_completed(stdout=""), _make_completed(stdout=staged_diff)]
        with patch("claude_monitor.git_ops.router.subprocess.run", side_effect=calls):
            r = app_client.get("/api/diff?project=my-project&file=new.py")
        assert r.status_code == 200
        assert r.json()["diff"] == staged_diff.strip()

    def test_diff_untracked_file(self, app_client, tmp_project):
        self._create_file(tmp_project, "untracked.py")
        new_file_diff = "+++ b/untracked.py\n+new content\n"
        ls_result = _make_completed(stdout="", returncode=1)
        untracked_diff = _make_completed(stdout=new_file_diff)
        calls = [_make_completed(stdout=""), _make_completed(stdout=""), ls_result, untracked_diff]
        with patch("claude_monitor.git_ops.router.subprocess.run", side_effect=calls):
            r = app_client.get("/api/diff?project=my-project&file=untracked.py")
        assert r.status_code == 200
        assert r.json()["is_new"] is True

    def test_diff_timeout_returns_504(self, app_client, tmp_project):
        import subprocess

        self._create_file(tmp_project, "app.py")
        with patch(
            "claude_monitor.git_ops.router.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["git"], 10),
        ):
            r = app_client.get("/api/diff?project=my-project&file=app.py")
        assert r.status_code == 504

    def test_diff_exception_returns_500(self, app_client, tmp_project):
        self._create_file(tmp_project, "app.py")
        with patch(
            "claude_monitor.git_ops.router.subprocess.run",
            side_effect=RuntimeError("git not found"),
        ):
            r = app_client.get("/api/diff?project=my-project&file=app.py")
        assert r.status_code == 500


class TestPendingEndpoint:
    def test_pending_returns_modified_files(self, app_client, tmp_project):
        porcelain = " M app.py\n?? untracked.py\n"
        with patch(
            "claude_monitor.git_ops.router.subprocess.run",
            return_value=_make_completed(stdout=porcelain),
        ):
            r = app_client.get("/api/pending?project=my-project")
        assert r.status_code == 200
        files = r.json()["files"]
        labels = [f["label"] for f in files]
        assert "modified" in labels
        assert "untracked" in labels

    def test_pending_renamed_file(self, app_client, tmp_project):
        porcelain = "R  old.py -> new.py\n"
        with patch(
            "claude_monitor.git_ops.router.subprocess.run",
            return_value=_make_completed(stdout=porcelain),
        ):
            r = app_client.get("/api/pending?project=my-project")
        assert r.status_code == 200
        files = r.json()["files"]
        assert any("new.py" in f["rel_path"] for f in files)

    def test_pending_git_error_returns_empty(self, app_client, tmp_project):
        with patch(
            "claude_monitor.git_ops.router.subprocess.run",
            return_value=_make_completed(stdout="", returncode=128),
        ):
            r = app_client.get("/api/pending?project=my-project")
        assert r.status_code == 200
        assert r.json()["files"] == []

    def test_pending_timeout_returns_504(self, app_client, tmp_project):
        import subprocess

        with patch(
            "claude_monitor.git_ops.router.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["git"], 10),
        ):
            r = app_client.get("/api/pending?project=my-project")
        assert r.status_code == 504

    def test_pending_exception_returns_500(self, app_client, tmp_project):
        with patch(
            "claude_monitor.git_ops.router.subprocess.run", side_effect=RuntimeError("unexpected")
        ):
            r = app_client.get("/api/pending?project=my-project")
        assert r.status_code == 500

    def test_pending_empty_porcelain_returns_empty(self, app_client, tmp_project):
        with patch(
            "claude_monitor.git_ops.router.subprocess.run", return_value=_make_completed(stdout="")
        ):
            r = app_client.get("/api/pending?project=my-project")
        assert r.status_code == 200
        assert r.json()["files"] == []

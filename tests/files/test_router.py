"""Tests for file operation endpoints."""

import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _make_completed(stdout: str = "", returncode: int = 0) -> CompletedProcess:
    cp = MagicMock(spec=CompletedProcess)
    cp.stdout = stdout
    cp.stderr = ""
    cp.returncode = returncode
    return cp


def test_browse_home(app_client):
    r = app_client.get("/api/browse")
    assert r.status_code == 200
    body = r.json()
    assert "current" in body
    assert "dirs" in body
    assert "parent" in body


def test_browse_invalid_path(app_client):
    r = app_client.get("/api/browse?path=/nonexistent/path/xyz")
    assert r.status_code == 400


def test_browse_specific_path(app_client, tmp_path):
    sub = tmp_path / "subdir"
    sub.mkdir()
    r = app_client.get(f"/api/browse?path={tmp_path}")
    assert r.status_code == 200
    body = r.json()
    assert str(sub) in body["dirs"]


def test_browse_permission_error(app_client, tmp_path):
    restricted = tmp_path / "restricted"
    restricted.mkdir()
    restricted.chmod(0o000)
    try:
        r = app_client.get(f"/api/browse?path={restricted}")
        assert r.status_code == 403
    finally:
        restricted.chmod(0o755)


def test_file_preview_non_md_returns_400(app_client, tmp_path):
    f = tmp_path / "script.py"
    f.write_text("print('hi')", encoding="utf-8")
    r = app_client.get(f"/api/file-preview?path={f}")
    assert r.status_code == 400


def test_file_preview_missing_file_returns_404(app_client):
    r = app_client.get("/api/file-preview?path=/nonexistent/file.md")
    assert r.status_code == 404


def test_file_preview_md_file(app_client, tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# Hello\n\nWorld.", encoding="utf-8")
    r = app_client.get(f"/api/file-preview?path={f}")
    assert r.status_code == 200
    body = r.json()
    assert "content" in body
    assert "Hello" in body["content"]
    assert body["truncated"] is False


def test_delete_file_unknown_project_returns_404(app_client):
    r = app_client.delete("/api/file?project=does-not-exist&path=some.txt")
    assert r.status_code == 404


def test_delete_tracked_file_is_rejected(app_client, tmp_project):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=str(tmp_project), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_project), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_project), check=True)
    test_file = tmp_project / "tracked.txt"
    test_file.write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=str(tmp_project), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add file"], cwd=str(tmp_project), check=True)
    r = app_client.delete(f"/api/file?project=my-project&path={test_file}")
    assert r.status_code in (400, 404)


class TestDeleteFileEndpoint:
    def test_delete_untracked_file_succeeds(self, app_client, tmp_project):
        untracked = tmp_project / "to_delete.txt"
        untracked.write_text("temp content")
        with patch(
            "claude_monitor.files.router.subprocess.run",
            return_value=_make_completed(stdout="", returncode=1),
        ):
            r = app_client.delete(f"/api/file?project=my-project&path={untracked}")
        assert r.status_code == 200
        assert "deleted" in r.json()
        assert not untracked.exists()

    def test_delete_nonexistent_file_returns_404(self, app_client, tmp_project):
        r = app_client.delete("/api/file?project=my-project&path=nonexistent.txt")
        assert r.status_code == 404

    def test_delete_timeout_returns_504(self, app_client, tmp_project):
        import subprocess

        f = tmp_project / "some.txt"
        f.write_text("x")
        with patch(
            "claude_monitor.files.router.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["git"], 5),
        ):
            r = app_client.delete(f"/api/file?project=my-project&path={f}")
        assert r.status_code == 504

    def test_delete_path_outside_project_returns_400(self, app_client, tmp_project, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("x")
        r = app_client.delete(f"/api/file?project=my-project&path={outside}")
        assert r.status_code == 400


class TestResolveProjectPath:
    def test_returns_path_for_known_project(self, tmp_project):
        from claude_monitor.files import router as files_router

        result = files_router._resolve_project_path("my-project")
        assert result is not None
        assert result.is_dir()

    def test_returns_none_for_unknown_project(self):
        from claude_monitor.files import router as files_router

        result = files_router._resolve_project_path("totally-unknown-project-xyz")
        assert result is None


class TestValidateFileWithinProject:
    def test_raises_nothing_for_file_inside_project(self, tmp_project):
        from claude_monitor.files import router as files_router
        from fastapi.responses import JSONResponse

        file_inside = (tmp_project / "app.py").resolve()
        # Should not raise
        result = files_router._validate_file_within_project(file_inside, tmp_project)
        assert result is None

    def test_returns_error_response_for_file_outside(self, tmp_project, tmp_path):
        from claude_monitor.files import router as files_router

        outside = (tmp_path / "other" / "secret.txt").resolve()
        result = files_router._validate_file_within_project(outside, tmp_project)
        assert result is not None  # Should return error JSONResponse


class TestCheckGitTracked:
    @pytest.mark.asyncio
    async def test_returns_true_when_git_ls_files_returncode_zero(self, tmp_project):
        from claude_monitor.files import router as files_router

        ls_result = _make_completed(stdout="app.py", returncode=0)
        with patch("claude_monitor.files.router.asyncio.to_thread", return_value=ls_result):
            result = await files_router._check_git_tracked(tmp_project / "app.py", tmp_project)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_git_ls_files_nonzero(self, tmp_project):
        from claude_monitor.files import router as files_router

        ls_result = _make_completed(stdout="", returncode=1)
        with patch("claude_monitor.files.router.asyncio.to_thread", return_value=ls_result):
            result = await files_router._check_git_tracked(tmp_project / "untracked.py", tmp_project)
        assert result is False


class TestAsyncSubprocessSafety:
    """
    Verify subprocess.run in delete_file is called via asyncio.to_thread.

    Calling subprocess.run directly inside an async handler blocks the event
    loop while git ls-files runs. asyncio.to_thread offloads it to a thread.

    Red: would fail if subprocess.run were called directly.
    Green: passes after wrapping with asyncio.to_thread.
    """

    def test_delete_file_subprocess_called_via_to_thread(self, app_client, tmp_project):
        f = tmp_project / "to_delete.txt"
        f.write_text("content")
        with patch("claude_monitor.files.router.asyncio.to_thread") as mock_to_thread:
            # returncode=1 means untracked — allowed to delete
            mock_to_thread.return_value = MagicMock(returncode=1, stdout="", stderr="")
            r = app_client.delete(f"/api/file?project=my-project&path={f}")
        assert r.status_code == 200
        mock_to_thread.assert_called_once()
        assert mock_to_thread.call_args[0][0] is subprocess.run

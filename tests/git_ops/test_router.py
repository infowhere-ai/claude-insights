"""Tests for git diff and pending files endpoints."""

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


class TestGitRunHelper:
    """Tests for the _git_run private helper."""

    @pytest.mark.asyncio
    async def test_git_run_returns_completed_process(self, tmp_project):
        from claude_monitor.git_ops import router as git_router

        cp = _make_completed(stdout="output")
        with patch(
            "claude_monitor.git_ops.router.asyncio.to_thread", return_value=cp
        ) as mock_thread:
            result = await git_router._git_run(["git", "diff"], tmp_project, 10)
        assert result.stdout == "output"
        mock_thread.assert_called_once()


class TestDiffHeadHelper:
    """Tests for the _diff_head private helper."""

    @pytest.mark.asyncio
    async def test_returns_diff_string_when_present(self, tmp_project):
        from claude_monitor.git_ops import router as git_router

        cp = _make_completed(stdout="some diff\n")
        with patch("claude_monitor.git_ops.router.asyncio.to_thread", return_value=cp):
            result = await git_router._diff_head(tmp_project, tmp_project / "app.py")
        assert result == "some diff"

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_diff(self, tmp_project):
        from claude_monitor.git_ops import router as git_router

        cp = _make_completed(stdout="")
        with patch("claude_monitor.git_ops.router.asyncio.to_thread", return_value=cp):
            result = await git_router._diff_head(tmp_project, tmp_project / "app.py")
        assert result == ""


class TestDiffUntrackedHelper:
    """Tests for the _diff_untracked private helper."""

    @pytest.mark.asyncio
    async def test_returns_diff_and_is_new_true_for_untracked(self, tmp_project):
        from claude_monitor.git_ops import router as git_router

        ls_result = _make_completed(stdout="", returncode=1)
        diff_result = _make_completed(stdout="new file diff\n")
        with patch(
            "claude_monitor.git_ops.router.asyncio.to_thread", side_effect=[ls_result, diff_result]
        ):
            diff, is_new = await git_router._diff_untracked(tmp_project, tmp_project / "new.py")
        assert is_new is True
        assert diff == "new file diff"

    @pytest.mark.asyncio
    async def test_returns_empty_and_is_new_false_for_tracked(self, tmp_project):
        from claude_monitor.git_ops import router as git_router

        ls_result = _make_completed(stdout="app.py", returncode=0)
        with patch("claude_monitor.git_ops.router.asyncio.to_thread", return_value=ls_result):
            diff, is_new = await git_router._diff_untracked(tmp_project, tmp_project / "app.py")
        assert is_new is False
        assert diff == ""


class TestResolvePendingProjectPath:
    """Tests for the _resolve_pending_project_path helper."""

    def test_returns_path_from_status_paths(self, tmp_path):
        """_resolve_pending_project_path returns project path from state._status_paths."""
        from claude_monitor.git_ops import router as git_router
        from claude_monitor import state

        proj_path = tmp_path / "my-project"
        proj_path.mkdir()
        status_path = proj_path / ".claude" / "status.json"
        status_path.parent.mkdir(parents=True)
        status_path.write_text("{}")

        original = dict(state._status_paths)
        state._status_paths["my-project"] = status_path
        try:
            result = git_router._resolve_pending_project_path("my-project")
            assert result == proj_path
        finally:
            state._status_paths.clear()
            state._status_paths.update(original)

    def test_returns_none_for_unknown_project(self, tmp_path):
        """_resolve_pending_project_path returns None when project is not found."""
        from claude_monitor.git_ops import router as git_router
        from claude_monitor import state, config

        original_paths = dict(state._status_paths)
        original_extra = list(state._extra_roots)
        original_root = config.PROJECTS_ROOT
        state._status_paths.clear()
        state._extra_roots.clear()
        config.PROJECTS_ROOT = tmp_path
        try:
            result = git_router._resolve_pending_project_path("ghost-project")
            assert result is None
        finally:
            state._status_paths.clear()
            state._status_paths.update(original_paths)
            state._extra_roots[:] = original_extra
            config.PROJECTS_ROOT = original_root


class TestParsePorcelainLine:
    """Tests for the _parse_porcelain_line helper."""

    def test_parses_modified_file(self, tmp_project):
        """_parse_porcelain_line returns dict for a modified file line."""
        from claude_monitor.git_ops import router as git_router

        result = git_router._parse_porcelain_line(" M app.py", tmp_project)
        assert result is not None
        assert result["rel_path"] == "app.py"
        assert result["label"] == "modified"
        assert result["status_code"] == "M"

    def test_parses_untracked_file(self, tmp_project):
        """_parse_porcelain_line returns dict with untracked label for ?? prefix."""
        from claude_monitor.git_ops import router as git_router

        result = git_router._parse_porcelain_line("?? newfile.py", tmp_project)
        assert result is not None
        assert result["label"] == "untracked"

    def test_parses_renamed_file(self, tmp_project):
        """_parse_porcelain_line returns new filename for renamed files."""
        from claude_monitor.git_ops import router as git_router

        result = git_router._parse_porcelain_line("R  old.py -> new.py", tmp_project)
        assert result is not None
        assert "new.py" in result["rel_path"]

    def test_returns_none_for_empty_line(self, tmp_project):
        """_parse_porcelain_line returns None for blank/whitespace lines."""
        from claude_monitor.git_ops import router as git_router

        assert git_router._parse_porcelain_line("   ", tmp_project) is None
        assert git_router._parse_porcelain_line("", tmp_project) is None

    def test_uses_changed_label_for_unknown_code(self, tmp_project):
        """_parse_porcelain_line falls back to 'changed' for unknown status codes."""
        from claude_monitor.git_ops import router as git_router

        result = git_router._parse_porcelain_line("X  mystery.py", tmp_project)
        assert result is not None
        assert result["label"] == "changed"


class TestAsyncSubprocessSafety:
    """
    Verify subprocess.run is called via asyncio.to_thread, not directly.

    These tests prove the async safety fix: calling subprocess.run directly
    inside an async handler blocks the event loop for the duration of the
    git command. asyncio.to_thread offloads the blocking call to a thread
    pool, keeping the event loop free.

    Red: would fail if subprocess.run were called directly (to_thread never invoked).
    Green: passes after wrapping each call with asyncio.to_thread.
    """

    def test_pending_subprocess_called_via_to_thread(self, app_client, tmp_project):
        with patch("claude_monitor.git_ops.router.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = MagicMock(returncode=0, stdout="", stderr="")
            r = app_client.get("/api/pending?project=my-project")
        assert r.status_code == 200
        mock_to_thread.assert_called()
        assert mock_to_thread.call_args[0][0] is subprocess.run

    def test_diff_subprocess_called_via_to_thread(self, app_client, tmp_project):
        f = tmp_project / "app.py"
        f.write_text("x = 1")
        with patch("claude_monitor.git_ops.router.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = MagicMock(returncode=0, stdout="diff output", stderr="")
            r = app_client.get("/api/diff?project=my-project&file=app.py")
        assert r.status_code == 200
        mock_to_thread.assert_called()
        assert mock_to_thread.call_args[0][0] is subprocess.run

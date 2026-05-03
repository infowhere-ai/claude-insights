"""Unit tests for project discovery and status reading."""

import asyncio
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from claude_monitor.projects import service as project_service
from claude_monitor import state
from claude_monitor.core import broadcast as broadcast_mod


class TestReadStatus:
    def test_reads_valid_json(self, tmp_path):
        f = tmp_path / "status.json"
        f.write_text('{"state": "working", "tool": "Read"}')
        result = project_service.read_status(f)
        assert result == {"state": "working", "tool": "Read"}

    def test_returns_none_on_missing_file(self, tmp_path):
        result = project_service.read_status(tmp_path / "missing.json")
        assert result is None

    def test_returns_none_on_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not valid json {{{")
        result = project_service.read_status(f)
        assert result is None

    def test_returns_none_on_empty_file(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("")
        result = project_service.read_status(f)
        assert result is None


class TestBroadcast:
    def test_puts_data_in_all_queues(self):
        q1 = asyncio.Queue(maxsize=10)
        q2 = asyncio.Queue(maxsize=10)
        original = list(state._sse_clients)
        state._sse_clients.clear()
        state._sse_clients.extend([q1, q2])
        try:
            broadcast_mod.broadcast({"type": "update", "project_name": "test"})
            assert q1.qsize() == 1
            assert q2.qsize() == 1
            assert q1.get_nowait()["type"] == "update"
        finally:
            state._sse_clients.clear()
            state._sse_clients.extend(original)

    def test_ignores_full_queues(self):
        q = asyncio.Queue(maxsize=1)
        q.put_nowait({"existing": True})
        original = list(state._sse_clients)
        state._sse_clients.clear()
        state._sse_clients.append(q)
        try:
            broadcast_mod.broadcast({"type": "update"})
            assert q.qsize() == 1
        finally:
            state._sse_clients.clear()
            state._sse_clients.extend(original)

    def test_broadcasts_to_no_clients(self):
        original = list(state._sse_clients)
        state._sse_clients.clear()
        try:
            broadcast_mod.broadcast({"type": "update"})
        finally:
            state._sse_clients.extend(original)


class TestCollectRoot:
    """Tests for the _collect_root helper extracted from discover."""

    def test_finds_status_json_in_subdirectory(self, tmp_path):
        """_collect_root populates candidates with name→status_path for found projects."""
        proj = tmp_path / "my-project"
        claude_dir = proj / ".claude"
        claude_dir.mkdir(parents=True)
        status = claude_dir / "status.json"
        status.write_text('{"state": "idle"}')

        candidates: dict = {}
        pending: set = set()
        project_service._collect_root(tmp_path, candidates, pending)

        assert "my-project" in candidates
        assert candidates["my-project"] == status

    def test_does_not_overwrite_existing_candidate(self, tmp_path):
        """_collect_root skips projects already in candidates dict."""
        proj = tmp_path / "existing"
        claude_dir = proj / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "status.json").write_text('{"state": "idle"}')

        original_path = tmp_path / "other" / ".claude" / "status.json"
        candidates: dict = {"existing": original_path}
        pending: set = set()
        project_service._collect_root(tmp_path, candidates, pending)

        assert candidates["existing"] == original_path

    def test_adds_to_pending_when_no_status_json(self, tmp_path):
        """_collect_root adds dirs with .claude but no status.json to pending set."""
        proj = tmp_path / "pending-project"
        (proj / ".claude").mkdir(parents=True)
        # No status.json

        candidates: dict = {}
        pending: set = set()
        project_service._collect_root(tmp_path, candidates, pending)

        assert "pending-project" in pending
        assert "pending-project" not in candidates

    def test_ignores_dirs_without_claude_subdir(self, tmp_path):
        """_collect_root ignores directories that have no .claude subdirectory."""
        (tmp_path / "plain-dir").mkdir()
        candidates: dict = {}
        pending: set = set()
        project_service._collect_root(tmp_path, candidates, pending)

        assert "plain-dir" not in candidates
        assert "plain-dir" not in pending


class TestDiscover:
    def _make_project(self, root: Path, name: str, status: dict | None = None) -> Path:
        proj = root / name
        claude_dir = proj / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "status.json").write_text(
            json.dumps(status or {"state": "idle"}), encoding="utf-8"
        )
        return proj

    def test_discovers_projects_with_status_json(self, tmp_path):
        self._make_project(tmp_path, "project-a")
        self._make_project(tmp_path, "project-b")
        original_paths = dict(state._status_paths)
        state._status_paths.clear()
        try:
            from claude_monitor import config as cfg

            original_root = cfg.PROJECTS_ROOT
            original_extra = list(state._extra_roots)
            cfg.PROJECTS_ROOT = tmp_path
            state._extra_roots.clear()
            try:
                project_service.discover()
                assert "project-a" in state._status_paths
                assert "project-b" in state._status_paths
            finally:
                cfg.PROJECTS_ROOT = original_root
                state._extra_roots[:] = original_extra
        finally:
            state._status_paths.clear()
            state._status_paths.update(original_paths)

    def test_does_not_discover_without_status_json(self, tmp_path):
        proj = tmp_path / "no-status"
        (proj / ".claude").mkdir(parents=True)
        original_paths = dict(state._status_paths)
        state._status_paths.clear()
        try:
            from claude_monitor import config as cfg

            original_root = cfg.PROJECTS_ROOT
            original_extra = list(state._extra_roots)
            cfg.PROJECTS_ROOT = tmp_path
            state._extra_roots.clear()
            try:
                project_service.discover()
                assert "no-status" not in state._status_paths
            finally:
                cfg.PROJECTS_ROOT = original_root
                state._extra_roots[:] = original_extra
        finally:
            state._status_paths.clear()
            state._status_paths.update(original_paths)

    def test_does_not_discover_without_claude_dir(self, tmp_path):
        (tmp_path / "plain-dir").mkdir()
        original_paths = dict(state._status_paths)
        state._status_paths.clear()
        try:
            from claude_monitor import config as cfg

            original_root = cfg.PROJECTS_ROOT
            original_extra = list(state._extra_roots)
            cfg.PROJECTS_ROOT = tmp_path
            state._extra_roots.clear()
            try:
                project_service.discover()
                assert "plain-dir" not in state._status_paths
            finally:
                cfg.PROJECTS_ROOT = original_root
                state._extra_roots[:] = original_extra
        finally:
            state._status_paths.clear()
            state._status_paths.update(original_paths)

    def test_filters_subprojects(self, tmp_path):
        parent = self._make_project(tmp_path, "parent-project")
        sub_dir = parent / "sub"
        (sub_dir / ".claude").mkdir(parents=True)
        (sub_dir / ".claude" / "status.json").write_text('{"state": "idle"}')

        original_paths = dict(state._status_paths)
        state._status_paths.clear()
        try:
            from claude_monitor import config as cfg

            original_root = cfg.PROJECTS_ROOT
            original_extra = list(state._extra_roots)
            cfg.PROJECTS_ROOT = tmp_path
            state._extra_roots.clear()
            try:
                project_service.discover()
                assert "parent-project" in state._status_paths
                assert "sub" not in state._status_paths
            finally:
                cfg.PROJECTS_ROOT = original_root
                state._extra_roots[:] = original_extra
        finally:
            state._status_paths.clear()
            state._status_paths.update(original_paths)

"""Unit tests for project discovery and status reading."""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

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

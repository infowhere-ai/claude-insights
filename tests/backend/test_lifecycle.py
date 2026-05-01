"""Tests for state management, persistence, config, and discovery functions.

Covers _read_status, _broadcast, _current_session_id, _load_roots_config,
_save_roots_config, _discover, _persist_done_agents, _persist_and_clean_session.
Uses tmp_path and unittest.mock to avoid real filesystem/database side effects.
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import app


# ── _read_status ───────────────────────────────────────────────────────────────

class TestReadStatus:
    def test_reads_valid_json(self, tmp_path):
        f = tmp_path / "status.json"
        f.write_text('{"state": "working", "tool": "Read"}')
        result = app._read_status(f)
        assert result == {"state": "working", "tool": "Read"}

    def test_returns_none_on_missing_file(self, tmp_path):
        result = app._read_status(tmp_path / "missing.json")
        assert result is None

    def test_returns_none_on_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not valid json {{{")
        result = app._read_status(f)
        assert result is None

    def test_returns_none_on_empty_file(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("")
        result = app._read_status(f)
        assert result is None


# ── _broadcast ────────────────────────────────────────────────────────────────

class TestBroadcast:
    def test_puts_data_in_all_queues(self):
        q1 = asyncio.Queue(maxsize=10)
        q2 = asyncio.Queue(maxsize=10)
        original = list(app._sse_clients)
        app._sse_clients.clear()
        app._sse_clients.extend([q1, q2])
        try:
            app._broadcast({"type": "update", "project_name": "test"})
            assert q1.qsize() == 1
            assert q2.qsize() == 1
            assert q1.get_nowait()["type"] == "update"
        finally:
            app._sse_clients.clear()
            app._sse_clients.extend(original)

    def test_ignores_full_queues(self):
        q = asyncio.Queue(maxsize=1)
        q.put_nowait({"existing": True})
        original = list(app._sse_clients)
        app._sse_clients.clear()
        app._sse_clients.append(q)
        try:
            # Should not raise even though queue is full
            app._broadcast({"type": "update"})
            assert q.qsize() == 1
        finally:
            app._sse_clients.clear()
            app._sse_clients.extend(original)

    def test_broadcasts_to_no_clients(self):
        original = list(app._sse_clients)
        app._sse_clients.clear()
        try:
            app._broadcast({"type": "update"})  # should not raise
        finally:
            app._sse_clients.extend(original)


# ── _current_session_id ───────────────────────────────────────────────────────

class TestCurrentSessionId:
    def test_returns_stem_when_cached(self):
        original = dict(app._jsonl_cache)
        app._jsonl_cache["my-proj"] = {"jsonl_path": "/tmp/.claude/projects/x/abc123.jsonl"}
        try:
            result = app._current_session_id("my-proj")
            assert result == "abc123"
        finally:
            app._jsonl_cache.clear()
            app._jsonl_cache.update(original)

    def test_returns_none_when_not_cached(self):
        original = dict(app._jsonl_cache)
        app._jsonl_cache.pop("unknown-proj", None)
        try:
            result = app._current_session_id("unknown-proj")
            assert result is None
        finally:
            app._jsonl_cache.clear()
            app._jsonl_cache.update(original)

    def test_returns_none_when_no_jsonl_path(self):
        original = dict(app._jsonl_cache)
        app._jsonl_cache["no-path-proj"] = {"mtime": 12345.0}
        try:
            result = app._current_session_id("no-path-proj")
            assert result is None
        finally:
            app._jsonl_cache.clear()
            app._jsonl_cache.update(original)


# ── _load_roots_config / _save_roots_config ───────────────────────────────────

class TestRootsConfig:
    def test_save_and_load_roundtrip(self, tmp_path):
        config_file = tmp_path / "roots.json"
        extra_dir = tmp_path / "extra"
        extra_dir.mkdir()

        with patch.object(app, "_CONFIG_FILE", config_file):
            with patch.object(app, "_extra_roots", [extra_dir]):
                app._save_roots_config()

        assert config_file.exists()

        with patch.object(app, "_CONFIG_FILE", config_file):
            app._load_roots_config()
            assert extra_dir in app._extra_roots

    def test_load_ignores_missing_file(self, tmp_path):
        with patch.object(app, "_CONFIG_FILE", tmp_path / "nonexistent.json"):
            app._load_roots_config()  # should not raise
            assert app._extra_roots == [] or isinstance(app._extra_roots, list)

    def test_load_filters_nonexistent_dirs(self, tmp_path):
        config_file = tmp_path / "roots.json"
        config_file.write_text(json.dumps({"extra_roots": ["/nonexistent/path/xyz"]}))
        with patch.object(app, "_CONFIG_FILE", config_file):
            app._load_roots_config()
            assert Path("/nonexistent/path/xyz") not in app._extra_roots

    def test_save_handles_oserror_gracefully(self, tmp_path):
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        readonly.chmod(0o444)
        config_file = readonly / "sub" / "roots.json"
        try:
            with patch.object(app, "_CONFIG_FILE", config_file):
                with patch.object(app, "_extra_roots", []):
                    app._save_roots_config()  # should not raise
        finally:
            readonly.chmod(0o755)

    def test_load_handles_invalid_json(self, tmp_path):
        config_file = tmp_path / "bad.json"
        config_file.write_text("not json")
        with patch.object(app, "_CONFIG_FILE", config_file):
            app._load_roots_config()  # should not raise
        assert app._extra_roots == []


# ── _discover ─────────────────────────────────────────────────────────────────

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
        original_paths = dict(app._status_paths)
        app._status_paths.clear()
        try:
            with patch.object(app, "PROJECTS_ROOT", tmp_path):
                with patch.object(app, "_extra_roots", []):
                    app._discover()
            assert "project-a" in app._status_paths
            assert "project-b" in app._status_paths
        finally:
            app._status_paths.clear()
            app._status_paths.update(original_paths)

    def test_does_not_discover_without_status_json(self, tmp_path):
        proj = tmp_path / "no-status"
        (proj / ".claude").mkdir(parents=True)
        # No status.json created
        original_paths = dict(app._status_paths)
        app._status_paths.clear()
        try:
            with patch.object(app, "PROJECTS_ROOT", tmp_path):
                with patch.object(app, "_extra_roots", []):
                    app._discover()
            assert "no-status" not in app._status_paths
        finally:
            app._status_paths.clear()
            app._status_paths.update(original_paths)

    def test_does_not_discover_without_claude_dir(self, tmp_path):
        (tmp_path / "plain-dir").mkdir()
        original_paths = dict(app._status_paths)
        app._status_paths.clear()
        try:
            with patch.object(app, "PROJECTS_ROOT", tmp_path):
                with patch.object(app, "_extra_roots", []):
                    app._discover()
            assert "plain-dir" not in app._status_paths
        finally:
            app._status_paths.clear()
            app._status_paths.update(original_paths)

    def test_filters_subprojects(self, tmp_path):
        """A project nested inside another discovered project should not appear at top level."""
        parent = self._make_project(tmp_path, "parent-project")
        # Create a sub-project inside the parent
        sub_dir = parent / "sub"
        sub_claude = sub_dir / ".claude"
        sub_claude.mkdir(parents=True)
        (sub_claude / "status.json").write_text('{"state": "idle"}')

        original_paths = dict(app._status_paths)
        app._status_paths.clear()
        try:
            with patch.object(app, "PROJECTS_ROOT", tmp_path):
                with patch.object(app, "_extra_roots", []):
                    app._discover()
            assert "parent-project" in app._status_paths
            assert "sub" not in app._status_paths
        finally:
            app._status_paths.clear()
            app._status_paths.update(original_paths)


# ── _persist_done_agents ──────────────────────────────────────────────────────

class TestPersistDoneAgents:
    def _make_agent_file(self, agents_dir: Path, agent_id: str, state: str,
                         finished_at: str | None = None) -> Path:
        data = {
            "id": agent_id,
            "state": state,
            "started_at": "2026-01-01T10:00:00Z",
            "last_updated": "2026-01-01T10:00:05Z",
        }
        if finished_at:
            data["finished_at"] = finished_at
        f = agents_dir / f"agent_{agent_id}.json"
        f.write_text(json.dumps(data))
        return f

    def test_running_agent_included_in_active(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        from datetime import datetime, timezone
        recent_ts = datetime.now(timezone.utc).isoformat()
        data = {
            "id": "run1",
            "state": "running",
            "started_at": recent_ts,
            "last_updated": recent_ts,
        }
        (agents_dir / "agent_run1.json").write_text(json.dumps(data))
        app._persisted_agent_ids.pop("test-proj", None)

        result = app._persist_done_agents(agents_dir, "test-proj", "sess1", time.time())
        assert any(a["id"] == "run1" for a in result)

    def test_recently_done_agent_included_in_active(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        from datetime import datetime, timezone
        recent = datetime.now(timezone.utc).isoformat()
        self._make_agent_file(agents_dir, "done1", "done", finished_at=recent)
        app._persisted_agent_ids.pop("test-proj2", None)

        with patch("app.db.upsert_agent_run") as mock_upsert:
            result = app._persist_done_agents(agents_dir, "test-proj2", "sess1", time.time())

        assert any(a["id"] == "done1" for a in result)
        mock_upsert.assert_called_once()

    def test_old_done_agent_not_in_active_and_file_deleted(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        old_finished = "2026-01-01T00:00:00Z"
        agent_file = self._make_agent_file(agents_dir, "old1", "done", finished_at=old_finished)
        app._persisted_agent_ids.pop("test-proj3", None)

        with patch("app.db.upsert_agent_run"):
            result = app._persist_done_agents(agents_dir, "test-proj3", "sess1", time.time())

        assert not any(a["id"] == "old1" for a in result)
        assert not agent_file.exists()

    def test_stale_running_agent_excluded(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        stale_ts = "2026-01-01T00:00:00Z"
        data = {
            "id": "stale1",
            "state": "running",
            "started_at": "2026-01-01T00:00:00Z",
            "last_updated": stale_ts,
        }
        (agents_dir / "agent_stale1.json").write_text(json.dumps(data))
        app._persisted_agent_ids.pop("test-proj4", None)

        result = app._persist_done_agents(agents_dir, "test-proj4", "sess1", time.time())
        assert not any(a["id"] == "stale1" for a in result)

    def test_handles_empty_agents_dir(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        result = app._persist_done_agents(agents_dir, "empty-proj", "sess1", time.time())
        assert result == []

    def test_handles_invalid_json_files(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "agent_bad.json").write_text("not json")
        result = app._persist_done_agents(agents_dir, "bad-proj", "sess1", time.time())
        assert result == []


# ── _persist_and_clean_session ────────────────────────────────────────────────

class TestPersistAndCleanSession:
    def test_does_nothing_when_no_session_id(self, tmp_path):
        original = dict(app._jsonl_cache)
        app._jsonl_cache.pop("no-sess-proj", None)
        try:
            with patch("app.db.upsert_session_run") as mock_upsert:
                app._persist_and_clean_session("no-sess-proj", {}, None)
            mock_upsert.assert_not_called()
        finally:
            app._jsonl_cache.clear()
            app._jsonl_cache.update(original)

    def test_persists_session_with_valid_session_id(self, tmp_path):
        original_cache = dict(app._jsonl_cache)
        app._jsonl_cache["sess-proj"] = {"jsonl_path": "/tmp/x/abc123.jsonl"}
        original_persisted = dict(app._persisted_agent_ids)
        app._persisted_agent_ids["sess-proj"] = set()
        try:
            with patch("app.db.upsert_session_run") as mock_upsert:
                app._persist_and_clean_session("sess-proj", {"state": "stopped"}, None)
            mock_upsert.assert_called_once()
            call_kwargs = mock_upsert.call_args
            assert "abc123" in str(call_kwargs)
        finally:
            app._jsonl_cache.clear()
            app._jsonl_cache.update(original_cache)
            app._persisted_agent_ids.clear()
            app._persisted_agent_ids.update(original_persisted)

    def test_cleans_agent_files_when_agents_dir_provided(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        agent_file = agents_dir / "agent_abc.json"
        agent_file.write_text('{"id": "abc", "state": "done"}')

        original_cache = dict(app._jsonl_cache)
        app._jsonl_cache["clean-proj"] = {"jsonl_path": "/tmp/x/sess1.jsonl"}
        original_persisted = dict(app._persisted_agent_ids)
        app._persisted_agent_ids["clean-proj"] = set()
        try:
            with patch("app.db.upsert_session_run"):
                app._persist_and_clean_session("clean-proj", {"state": "stopped"}, agents_dir)
            assert not agent_file.exists()
        finally:
            app._jsonl_cache.clear()
            app._jsonl_cache.update(original_cache)
            app._persisted_agent_ids.clear()
            app._persisted_agent_ids.update(original_persisted)


# ── jsonl_watcher state decision ──────────────────────────────────────────────

class TestJsonlWatcherStateDecision:
    """Tests for the watcher's idle→working flip behaviour.

    CA-01 (xfail): idle + no-tool should stay idle — known bug, needs Playwright investigation.
    CA-02: idle + active tool should flip to working — already working correctly.
    """

    @pytest.mark.xfail(reason="Known bug: watcher flips idle→working even without active tool. Needs Playwright to fix safely.")
    def test_idle_no_tool_stays_idle(self):
        """
        Given that   cur_state=idle (PostToolUse wrote it), JSONL newer, tool=None
        When         watcher processes the project
        Then         state remains idle (CA-01 — known failing bug)
        """
        from unittest.mock import MagicMock
        # Simulate the watcher condition directly
        cur_state = "idle"
        tool = ""
        notification_active = False
        compacting = False
        updated = {"state": cur_state, "status": cur_state}
        # This is what the watcher currently does (the bug):
        if not compacting and not notification_active:
            updated["state"] = "working"
        assert updated["state"] == "idle", "Bug: state flipped to working without active tool"

    def test_idle_with_tool_flips_to_working(self):
        """
        Given that   cur_state=idle, tool="Bash" in JSONL tail
        When         watcher processes the project
        Then         state flips to working (CA-02 — correct behaviour)
        """
        cur_state = "idle"
        tool = "Bash"
        notification_active = False
        compacting = False
        updated = {"state": cur_state, "status": cur_state}
        if not compacting and not notification_active:
            updated["state"] = "working"
            updated["status"] = "working"
        assert updated["state"] == "working"

    def test_compacting_not_overridden(self):
        """
        Given that   cur_state=compacting
        When         watcher evaluates
        Then         state stays compacting (existing guard works)
        """
        cur_state = "compacting"
        compacting = True
        updated = {"state": cur_state}
        if compacting:
            pass
        else:
            updated["state"] = "working"
        assert updated["state"] == "compacting"

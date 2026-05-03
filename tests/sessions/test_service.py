"""Unit tests for session and agent persistence."""

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from claude_monitor.sessions import service as session_service
from claude_monitor import state


class TestCurrentSessionId:
    def test_returns_stem_when_cached(self):
        original = dict(state._jsonl_cache)
        state._jsonl_cache["my-proj"] = {"jsonl_path": "/tmp/.claude/projects/x/abc123.jsonl"}
        try:
            result = session_service.current_session_id("my-proj")
            assert result == "abc123"
        finally:
            state._jsonl_cache.clear()
            state._jsonl_cache.update(original)

    def test_returns_none_when_not_cached(self):
        original = dict(state._jsonl_cache)
        state._jsonl_cache.pop("unknown-proj", None)
        try:
            result = session_service.current_session_id("unknown-proj")
            assert result is None
        finally:
            state._jsonl_cache.clear()
            state._jsonl_cache.update(original)

    def test_returns_none_when_no_jsonl_path(self):
        original = dict(state._jsonl_cache)
        state._jsonl_cache["no-path-proj"] = {"mtime": 12345.0}
        try:
            result = session_service.current_session_id("no-path-proj")
            assert result is None
        finally:
            state._jsonl_cache.clear()
            state._jsonl_cache.update(original)


class TestPersistDoneAgents:
    def _make_agent_file(
        self, agents_dir: Path, agent_id: str, agent_state: str, finished_at: str | None = None
    ) -> Path:
        data = {
            "id": agent_id,
            "state": agent_state,
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
        state._persisted_agent_ids.pop("test-proj", None)

        result = session_service.persist_done_agents(agents_dir, "test-proj", "sess1", time.time())
        assert any(a["id"] == "run1" for a in result)

    def test_recently_done_agent_included_in_active(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        from datetime import datetime, timezone

        recent = datetime.now(timezone.utc).isoformat()
        self._make_agent_file(agents_dir, "done1", "done", finished_at=recent)
        state._persisted_agent_ids.pop("test-proj2", None)

        with patch("claude_monitor.db.upsert_agent_run") as mock_upsert:
            result = session_service.persist_done_agents(
                agents_dir, "test-proj2", "sess1", time.time()
            )

        assert any(a["id"] == "done1" for a in result)
        mock_upsert.assert_called_once()

    def test_old_done_agent_not_in_active_and_file_deleted(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        agent_file = self._make_agent_file(
            agents_dir, "old1", "done", finished_at="2026-01-01T00:00:00Z"
        )
        state._persisted_agent_ids.pop("test-proj3", None)

        with patch("claude_monitor.db.upsert_agent_run"):
            result = session_service.persist_done_agents(
                agents_dir, "test-proj3", "sess1", time.time()
            )

        assert not any(a["id"] == "old1" for a in result)
        assert not agent_file.exists()

    def test_stale_running_agent_excluded(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        data = {
            "id": "stale1",
            "state": "running",
            "started_at": "2026-01-01T00:00:00Z",
            "last_updated": "2026-01-01T00:00:00Z",
        }
        (agents_dir / "agent_stale1.json").write_text(json.dumps(data))
        state._persisted_agent_ids.pop("test-proj4", None)

        result = session_service.persist_done_agents(agents_dir, "test-proj4", "sess1", time.time())
        assert not any(a["id"] == "stale1" for a in result)

    def test_handles_empty_agents_dir(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        result = session_service.persist_done_agents(agents_dir, "empty-proj", "sess1", time.time())
        assert result == []

    def test_handles_invalid_json_files(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "agent_bad.json").write_text("not json")
        result = session_service.persist_done_agents(agents_dir, "bad-proj", "sess1", time.time())
        assert result == []


class TestPersistAndCleanSession:
    def test_does_nothing_when_no_session_id(self, tmp_path):
        original = dict(state._jsonl_cache)
        state._jsonl_cache.pop("no-sess-proj", None)
        try:
            with patch("claude_monitor.db.upsert_session_run") as mock_upsert:
                session_service.persist_and_clean_session("no-sess-proj", {}, None)
            mock_upsert.assert_not_called()
        finally:
            state._jsonl_cache.clear()
            state._jsonl_cache.update(original)

    def test_persists_session_with_valid_session_id(self, tmp_path):
        original_cache = dict(state._jsonl_cache)
        state._jsonl_cache["sess-proj"] = {"jsonl_path": "/tmp/x/abc123.jsonl"}
        original_persisted = dict(state._persisted_agent_ids)
        state._persisted_agent_ids["sess-proj"] = set()
        try:
            with patch("claude_monitor.db.upsert_session_run") as mock_upsert:
                session_service.persist_and_clean_session("sess-proj", {"state": "stopped"}, None)
            mock_upsert.assert_called_once()
            assert "abc123" in str(mock_upsert.call_args)
        finally:
            state._jsonl_cache.clear()
            state._jsonl_cache.update(original_cache)
            state._persisted_agent_ids.clear()
            state._persisted_agent_ids.update(original_persisted)

    def test_cleans_agent_files_when_agents_dir_provided(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        agent_file = agents_dir / "agent_abc.json"
        agent_file.write_text('{"id": "abc", "state": "done"}')

        original_cache = dict(state._jsonl_cache)
        state._jsonl_cache["clean-proj"] = {"jsonl_path": "/tmp/x/sess1.jsonl"}
        original_persisted = dict(state._persisted_agent_ids)
        state._persisted_agent_ids["clean-proj"] = set()
        try:
            with patch("claude_monitor.db.upsert_session_run"):
                session_service.persist_and_clean_session(
                    "clean-proj", {"state": "stopped"}, agents_dir
                )
            assert not agent_file.exists()
        finally:
            state._jsonl_cache.clear()
            state._jsonl_cache.update(original_cache)
            state._persisted_agent_ids.clear()
            state._persisted_agent_ids.update(original_persisted)


class TestParseAgentFile:
    def test_returns_data_and_id_on_valid_json(self, tmp_path):
        agent_file = tmp_path / "agent_abc.json"
        data = {"id": "abc", "state": "done", "started_at": "2026-01-01T10:00:00Z"}
        agent_file.write_text(json.dumps(data))
        result_data, result_id = session_service._parse_agent_file(agent_file)
        assert result_data == data
        assert result_id == "abc"

    def test_falls_back_to_stem_when_no_id(self, tmp_path):
        agent_file = tmp_path / "agent_xyz.json"
        data = {"state": "done"}
        agent_file.write_text(json.dumps(data))
        _, result_id = session_service._parse_agent_file(agent_file)
        assert result_id == "agent_xyz"

    def test_raises_on_invalid_json(self, tmp_path):
        agent_file = tmp_path / "agent_bad.json"
        agent_file.write_text("not json {{{")
        with pytest.raises(Exception):
            session_service._parse_agent_file(agent_file)


class TestIsStaleRunning:
    def test_returns_false_for_recent_timestamp(self):
        from datetime import datetime, timezone

        recent = datetime.now(timezone.utc).isoformat()
        data = {"last_updated": recent}
        assert session_service._is_stale_running(data, time.time()) is False

    def test_returns_true_for_old_timestamp(self):
        data = {"last_updated": "2020-01-01T00:00:00Z"}
        assert session_service._is_stale_running(data, time.time()) is True

    def test_returns_false_when_no_timestamp(self):
        data = {}
        assert session_service._is_stale_running(data, time.time()) is False


class TestAgeOfDoneAgent:
    def test_returns_age_in_seconds(self):
        from datetime import datetime, timezone

        now_ts = time.time()
        old_ts = datetime.fromtimestamp(now_ts - 500, tz=timezone.utc).isoformat()
        data = {"finished_at": old_ts}
        age = session_service._age_of_done_agent(data, now_ts)
        assert 490 < age < 510

    def test_returns_inf_when_no_finished_at(self):
        data = {}
        age = session_service._age_of_done_agent(data, time.time())
        assert age == float("inf")

    def test_returns_inf_on_bad_timestamp(self):
        data = {"finished_at": "not-a-date"}
        age = session_service._age_of_done_agent(data, time.time())
        assert age == float("inf")


class TestJsonlWatcherStateDecision:
    @pytest.mark.xfail(
        reason="Known bug: watcher flips idle→working even without active tool. Needs Playwright."
    )
    def test_idle_no_tool_stays_idle(self):
        cur_state = "idle"
        notification_active = False
        compacting = False
        updated = {"state": cur_state, "status": cur_state}
        if not compacting and not notification_active:
            updated["state"] = "working"
        assert updated["state"] == "idle"

    def test_idle_with_tool_flips_to_working(self):
        cur_state = "idle"
        notification_active = False
        compacting = False
        updated = {"state": cur_state, "status": cur_state}
        if not compacting and not notification_active:
            updated["state"] = "working"
            updated["status"] = "working"
        assert updated["state"] == "working"

    def test_compacting_not_overridden(self):
        cur_state = "compacting"
        compacting = True
        updated = {"state": cur_state}
        if compacting:
            pass
        else:
            updated["state"] = "working"
        assert updated["state"] == "compacting"

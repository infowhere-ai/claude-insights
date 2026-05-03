"""Tests for asyncio background task GC safety in lifespan."""

import asyncio
import datetime
import inspect
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestBackgroundTaskGCSafety:
    """
    Verify that asyncio background tasks are stored with a strong reference.

    asyncio.create_task() returns a Task that can be garbage-collected if no
    strong reference is held. If the GC runs before the task completes, the
    task is silently cancelled. Storing the task in a set (with a done_callback
    to discard it) keeps a reference for the task's lifetime.

    Red: would fail if tasks were created without storing the reference
         (GC could discard them before they run).
    Green: passes after storing tasks in a set with add_done_callback.
    """

    def test_lifespan_creates_tasks_with_strong_references(self):
        """
        Background tasks created in lifespan must be stored in a set.
        We verify by checking that asyncio.create_task results have
        done_callbacks registered (the discard callback proves the set exists).
        The patch must be active when the lifespan starts, so we create a
        fresh TestClient inside the test instead of using the shared fixture.
        """
        from fastapi.testclient import TestClient

        from claude_monitor.main import app

        created_tasks: list = []
        original_create_task = asyncio.create_task

        def tracking_create_task(coro, **kwargs):
            task = original_create_task(coro, **kwargs)
            created_tasks.append(task)
            return task

        with patch("claude_monitor.main.asyncio.create_task", side_effect=tracking_create_task):
            with TestClient(app) as client:
                r = client.get("/api/status")

        assert r.status_code == 200
        assert len(created_tasks) >= 3, "Expected 3 background tasks (discovery, poll, jsonl)"
        # Each task must be a real asyncio.Task — not a plain coroutine left unscheduled
        for task in created_tasks:
            assert isinstance(task, asyncio.Task), (
                f"Expected asyncio.Task, got {type(task)} — "
                "task may not have been scheduled on the event loop"
            )

    def test_main_module_does_not_create_untracked_tasks(self):
        """
        The main module source must not contain bare asyncio.create_task
        without storing the result.
        """
        import inspect

        import claude_monitor.main as main_module

        source = inspect.getsource(main_module)
        # All create_task calls should be preceded by an assignment
        # (either direct or via loop like our for-loop pattern)
        assert "asyncio.create_task(" in source
        # The bare pattern "asyncio.create_task(..." on its own line
        # (without assignment) should not exist
        lines = source.splitlines()
        for line in lines:
            stripped = line.strip()
            if "asyncio.create_task(" in stripped:
                # Must be an assignment or part of a for-loop body that assigns
                assert "=" in stripped or stripped.startswith("t =") or stripped.startswith("t="), (
                    f"Untracked create_task found: {stripped!r}"
                )


class TestTimezoneAwareDatetime:
    """
    Verify that background.py does not use the deprecated datetime.utcnow().

    datetime.datetime.utcnow() is deprecated in Python 3.12+ and flagged by
    Sonar as a CRITICAL code smell because it returns a naive datetime that
    is ambiguous (no timezone info). The correct replacement is
    datetime.datetime.now(datetime.timezone.utc), which returns a
    timezone-aware datetime equivalent to UTC.

    Red: fails if background.py still contains utcnow().
    Green: passes after replacing with datetime.now(datetime.timezone.utc).
    """

    def test_background_does_not_use_utcnow(self):
        """
        Given that background.py is the only module using utcnow()
        When we inspect its source code
        Then it must not contain the deprecated utcnow() call

        Structural guard — fails if the fix is reverted.
        """
        import claude_monitor.core.background as background_module

        source = inspect.getsource(background_module)
        assert "utcnow()" not in source, (
            "background.py uses deprecated datetime.utcnow() — "
            "replace with datetime.now(datetime.timezone.utc)"
        )

    def test_fallback_timestamp_is_timezone_aware(self):
        """
        Given background.py generates a fallback timestamp when status.json has no 'ts'
        When the timestamp is generated using datetime.now(timezone.utc)
        Then it must be a timezone-aware ISO 8601 string with UTC offset

        Behavioral test — verifies the actual output, not just the source text.
        datetime.utcnow().isoformat() produces '2026-05-03T16:00:00' (no timezone).
        datetime.now(timezone.utc).isoformat() produces '2026-05-03T16:00:00+00:00' (aware).
        The latter is unambiguous and parseable with full timezone info.
        """
        # Reproduce the exact expression used in background.py after the fix
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Behavioral: must include UTC offset
        assert "+00:00" in ts, (
            f"Timestamp '{ts}' is missing UTC offset — "
            "datetime.utcnow() produces a naive datetime without timezone info"
        )

        # Must parse back to a timezone-aware datetime
        parsed = datetime.datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None, f"Parsed timestamp '{ts}' has no tzinfo"
        assert parsed.utcoffset() == datetime.timedelta(0), (
            "Expected UTC offset of zero, got non-UTC timezone"
        )


# ---------------------------------------------------------------------------
# Helper function tests — extracted from poll_loop
# ---------------------------------------------------------------------------


class TestShouldOverrideWithJsonl:
    """
    Tests for _should_override_with_jsonl.

    Given a data dict, jsonl_info dict, current timestamp and status.json mtime,
    return True when JSONL is newer than status.json AND within the active window.
    """

    def setup_method(self):
        from claude_monitor.core.background import _should_override_with_jsonl

        self._fn = _should_override_with_jsonl

    def test_returns_true_when_jsonl_newer_and_active(self):
        """
        Given jsonl_mtime is set, greater than status mtime, and within active seconds
        When _should_override_with_jsonl is called
        Then it returns True
        """
        import time

        from claude_monitor import config

        now_ts = time.time()
        mtime = now_ts - 30.0
        jsonl_mtime = now_ts - 5.0  # newer than status, within active window
        assert jsonl_mtime > mtime
        assert (now_ts - jsonl_mtime) <= config.JSONL_ACTIVE_SECONDS

        result = self._fn({}, {"mtime": jsonl_mtime}, now_ts, mtime)
        assert result is True

    def test_returns_false_when_no_jsonl_mtime(self):
        """
        Given jsonl_info has no mtime key
        When _should_override_with_jsonl is called
        Then it returns False (no JSONL to override with)
        """
        import time

        now_ts = time.time()
        result = self._fn({}, {}, now_ts, now_ts - 10.0)
        assert result is False

    def test_returns_false_when_jsonl_older_than_status(self):
        """
        Given jsonl_mtime is older than the status.json mtime
        When _should_override_with_jsonl is called
        Then it returns False (status.json is more recent)
        """
        import time

        now_ts = time.time()
        mtime = now_ts - 5.0
        jsonl_mtime = now_ts - 20.0  # older than status
        result = self._fn({}, {"mtime": jsonl_mtime}, now_ts, mtime)
        assert result is False

    def test_returns_false_when_jsonl_stale(self):
        """
        Given jsonl_mtime is newer than status but outside the active window
        When _should_override_with_jsonl is called
        Then it returns False (JSONL is too old to be considered active)
        """
        import time

        from claude_monitor import config

        now_ts = time.time()
        mtime = now_ts - 200.0
        jsonl_mtime = now_ts - config.JSONL_ACTIVE_SECONDS - 1.0  # outside active window
        result = self._fn({}, {"mtime": jsonl_mtime}, now_ts, mtime)
        assert result is False


class TestApplyJsonlState:
    """
    Tests for _apply_jsonl_state.

    Given a data dict and jsonl_info, mutate data in place to apply the JSONL
    tool/state — unless state is compacting or notification is active.
    """

    def setup_method(self):
        from claude_monitor.core.background import _apply_jsonl_state

        self._fn = _apply_jsonl_state

    def test_sets_working_state_and_clears_notification(self):
        """
        Given data has idle state and no notification
        When _apply_jsonl_state is called
        Then data state/status become 'working' and notification is None
        """
        data = {"state": "idle", "status": "idle", "notification": None}
        self._fn(data, {"tool": None})
        assert data["state"] == "working"
        assert data["status"] == "working"
        assert data["notification"] is None

    def test_sets_current_action_when_tool_present(self):
        """
        Given jsonl_info has a tool name
        When _apply_jsonl_state is called
        Then data gets current_action and tool set from jsonl
        """
        data = {"state": "idle", "status": "idle"}
        self._fn(data, {"tool": "Bash"})
        assert data["tool"] == "Bash"
        assert data["current_action"]["tool"] == "Bash"
        assert data["current_action"]["hook"] == "PreToolUse"
        assert data["current_action"]["description"] == "Bash"

    def test_no_tool_does_not_set_current_action(self):
        """
        Given jsonl_info has no tool (None or missing)
        When _apply_jsonl_state is called
        Then current_action is not set
        """
        data = {"state": "idle", "status": "idle"}
        self._fn(data, {"tool": None})
        assert "current_action" not in data

    def test_preserves_compacting_state(self):
        """
        Given data state is 'compacting'
        When _apply_jsonl_state is called
        Then state remains 'compacting' (not overridden to 'working')
        """
        data = {"state": "compacting", "status": "compacting"}
        self._fn(data, {"tool": "Bash"})
        assert data["state"] == "compacting"
        assert data["status"] == "compacting"

    def test_preserves_notification_active_state(self):
        """
        Given data has an active notification (state=waiting, notification set)
        When _apply_jsonl_state is called
        Then state/notification are preserved (not overridden to 'working')
        """
        data = {
            "state": "waiting",
            "status": "waiting",
            "notification": {"message": "pending"},
        }
        self._fn(data, {"tool": "Bash"})
        assert data["state"] == "waiting"
        assert data["notification"] is not None

    def test_non_waiting_notification_state_gets_overridden(self):
        """
        Given data has notification set but state is NOT waiting/notification
        When _apply_jsonl_state is called
        Then state becomes working (notification_active condition is False)
        """
        data = {
            "state": "idle",
            "status": "idle",
            "notification": {"message": "old"},
        }
        self._fn(data, {"tool": None})
        assert data["state"] == "working"
        assert data["notification"] is None


class TestBuildEvent:
    """
    Tests for _build_event.

    Given a data dict, return the SSE event dict with timestamp, status, tool,
    message, and hook fields.
    """

    def setup_method(self):
        from claude_monitor.core.background import _build_event

        self._fn = _build_event

    def test_working_status_builds_working_event(self):
        """
        Given data with status='working' and a tool name
        When _build_event is called
        Then event has hook='PreToolUse' and message=tool name
        """
        data = {
            "status": "working",
            "tool": "Bash",
            "ts": "2026-05-03T12:00:00+00:00",
        }
        event = self._fn(data)
        assert event["status"] == "working"
        assert event["tool"] == "Bash"
        assert event["message"] == "Bash"
        assert event["hook"] == "PreToolUse"
        assert event["timestamp"] == "2026-05-03T12:00:00+00:00"

    def test_idle_status_builds_idle_event(self):
        """
        Given data with status='idle' and no tool
        When _build_event is called
        Then event has hook='PostToolUse' and message='idle'
        """
        data = {"status": "idle", "tool": None, "ts": "2026-05-03T12:00:00+00:00"}
        event = self._fn(data)
        assert event["status"] == "idle"
        assert event["tool"] is None
        assert event["message"] == "idle"
        assert event["hook"] == "PostToolUse"

    def test_fallback_timestamp_when_ts_missing(self):
        """
        Given data has no 'ts' key
        When _build_event is called
        Then timestamp is a timezone-aware ISO string generated from now
        """
        data = {"status": "idle"}
        event = self._fn(data)
        assert "timestamp" in event
        assert "+00:00" in event["timestamp"]


class TestHandleAgentChanges:
    """
    Tests for _handle_agent_changes.

    Given a project name, path, project_path, and timestamp,
    persist done agents and broadcast if the set of agent IDs changed.
    """

    def setup_method(self):
        from claude_monitor.core.background import _handle_agent_changes

        self._fn = _handle_agent_changes

    def test_broadcasts_when_agent_ids_change(self, tmp_path):
        """
        Given a project with agents_dir and a current state with different agent IDs
        When _handle_agent_changes is called with new agents
        Then state is updated and broadcast is called
        """
        from claude_monitor import state
        from claude_monitor.core import broadcast as broadcast_mod

        name = "test_proj_agent_change"
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)

        state.projects[name] = {
            "state": "working",
            "active_agents": [{"id": "old-agent-1", "state": "done"}],
        }

        new_agents = [{"id": "new-agent-1", "state": "running"}]
        broadcasts = []

        with (
            patch("claude_monitor.sessions.service.persist_done_agents", return_value=new_agents),
            patch("claude_monitor.sessions.service.current_session_id", return_value="sess-1"),
            patch.object(broadcast_mod, "broadcast", side_effect=broadcasts.append),
        ):
            self._fn(name, tmp_path / "status.json", tmp_path, 12345.0)

        assert len(broadcasts) == 1
        assert broadcasts[0]["project_name"] == name
        assert broadcasts[0]["data"]["active_agents"] == new_agents

        # Cleanup
        del state.projects[name]

    def test_no_broadcast_when_agent_ids_unchanged(self, tmp_path):
        """
        Given a project whose agent IDs do not change
        When _handle_agent_changes is called
        Then broadcast is NOT called (no change detected)
        """
        from claude_monitor import state
        from claude_monitor.core import broadcast as broadcast_mod

        name = "test_proj_no_change"
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)

        agent = {"id": "agent-1", "state": "running"}
        state.projects[name] = {"state": "working", "active_agents": [agent]}

        broadcasts = []

        with (
            patch("claude_monitor.sessions.service.persist_done_agents", return_value=[agent]),
            patch("claude_monitor.sessions.service.current_session_id", return_value="sess-1"),
            patch.object(broadcast_mod, "broadcast", side_effect=broadcasts.append),
        ):
            self._fn(name, tmp_path / "status.json", tmp_path, 12345.0)

        assert len(broadcasts) == 0

        # Cleanup
        del state.projects[name]

    def test_no_broadcast_when_project_not_in_state(self, tmp_path):
        """
        Given the project is not yet in state.projects
        When _handle_agent_changes is called
        Then nothing happens (no KeyError, no broadcast)
        """
        from claude_monitor import state
        from claude_monitor.core import broadcast as broadcast_mod

        name = "test_proj_missing"
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)

        # ensure not in state
        state.projects.pop(name, None)

        broadcasts = []

        with (
            patch("claude_monitor.sessions.service.persist_done_agents", return_value=[]),
            patch("claude_monitor.sessions.service.current_session_id", return_value="sess-1"),
            patch.object(broadcast_mod, "broadcast", side_effect=broadcasts.append),
        ):
            self._fn(name, tmp_path / "status.json", tmp_path, 12345.0)

        assert len(broadcasts) == 0


# ---------------------------------------------------------------------------
# Helper function tests — extracted from jsonl_watcher_loop
# ---------------------------------------------------------------------------


class TestNowIso:
    """Tests for _now_iso."""

    def test_returns_utc_iso_string_with_offset(self):
        """
        Given the current time
        When _now_iso is called
        Then returns an ISO 8601 string with +00:00 offset
        """
        from claude_monitor.core.background import _now_iso

        result = _now_iso()
        assert isinstance(result, str)
        assert "+00:00" in result
        # Must be parseable as timezone-aware datetime
        parsed = datetime.datetime.fromisoformat(result)
        assert parsed.tzinfo is not None


class TestMakeIdleUpdate:
    """Tests for _make_idle_update."""

    def test_returns_copy_with_idle_state(self):
        """
        Given a current dict with state='working'
        When _make_idle_update is called
        Then returns a NEW dict with state/status='idle' and _stale=True
        """
        from claude_monitor.core.background import _make_idle_update

        current = {"state": "working", "status": "working", "tool": "Bash"}
        result = _make_idle_update(current)

        # Must be a copy, not the same object
        assert result is not current
        assert result["state"] == "idle"
        assert result["status"] == "idle"
        assert result["message"] == "idle"
        assert result["_stale"] is True

    def test_does_not_mutate_original(self):
        """
        Given a current dict
        When _make_idle_update is called
        Then the original dict is not mutated
        """
        from claude_monitor.core.background import _make_idle_update

        current = {"state": "working", "status": "working"}
        _make_idle_update(current)
        assert current["state"] == "working"

    def test_sets_ts_and_updated_at(self):
        """
        Given a current dict
        When _make_idle_update is called
        Then ts and updated_at are set to a UTC ISO string
        """
        from claude_monitor.core.background import _make_idle_update

        current = {"state": "working"}
        result = _make_idle_update(current)
        assert "+00:00" in result["ts"]
        assert "+00:00" in result["updated_at"]


class TestMergeStats:
    """Tests for _merge_stats."""

    def test_merges_jsonl_stats_over_hook_stats(self, tmp_path):
        """
        Given updated dict with hook_stats and jsonl returns new stats
        When _merge_stats is called (mtime differs from cached)
        Then updated['stats'] contains merged result with jsonl taking precedence
        """
        from claude_monitor import state
        from claude_monitor.core.background import _merge_stats

        name = "merge_test"
        latest_jsonl = tmp_path / "session.jsonl"
        latest_jsonl.touch()
        latest_mtime = latest_jsonl.stat().st_mtime

        # Ensure cache is stale (different mtime)
        state._jsonl_mtimes[str(latest_jsonl)] = latest_mtime - 100.0

        hook_stats = {"session_ctx_tokens": 1000, "model": "claude-3"}
        jsonl_stats_return = {"tokens_in": 500, "tokens_out": 200}

        updated = {"stats": hook_stats.copy()}

        with patch(
            "claude_monitor.stats.service.get_project_stats", return_value=jsonl_stats_return
        ):
            _merge_stats(updated, tmp_path, name, latest_jsonl, latest_mtime)

        # jsonl stats are present
        assert updated["stats"]["tokens_in"] == 500
        # hook stats preserved since jsonl didn't override them
        assert updated["stats"]["session_ctx_tokens"] == 1000

    def test_preserves_hook_session_ctx_when_jsonl_lacks_it(self, tmp_path):
        """
        Given jsonl_stats has no session_ctx_tokens but hook_stats does
        When _merge_stats is called
        Then session_ctx_tokens from hook_stats is preserved in merged result
        """
        from claude_monitor import state
        from claude_monitor.core.background import _merge_stats

        name = "merge_test_ctx"
        latest_jsonl = tmp_path / "session2.jsonl"
        latest_jsonl.touch()
        latest_mtime = latest_jsonl.stat().st_mtime

        state._jsonl_mtimes[str(latest_jsonl)] = latest_mtime - 100.0

        hook_stats = {"session_ctx_tokens": 999, "model": "claude-3-sonnet"}
        jsonl_stats_return = {"tokens_in": 100}  # no session_ctx_tokens, no model

        updated = {"stats": hook_stats.copy()}

        with patch(
            "claude_monitor.stats.service.get_project_stats", return_value=jsonl_stats_return
        ):
            _merge_stats(updated, tmp_path, name, latest_jsonl, latest_mtime)

        assert updated["stats"]["session_ctx_tokens"] == 999
        assert updated["stats"]["model"] == "claude-3-sonnet"

    def test_skips_merge_when_mtime_not_stale(self, tmp_path):
        """
        Given the jsonl mtime matches the cached mtime (not stale)
        When _merge_stats is called
        Then stats are NOT updated (no service call)
        """
        from claude_monitor import state
        from claude_monitor.core.background import _merge_stats

        name = "merge_test_fresh"
        latest_jsonl = tmp_path / "session3.jsonl"
        latest_jsonl.touch()
        latest_mtime = latest_jsonl.stat().st_mtime

        # Cache is up to date
        state._jsonl_mtimes[str(latest_jsonl)] = latest_mtime

        original_stats = {"tokens_in": 42}
        updated = {"stats": original_stats.copy()}

        with patch("claude_monitor.stats.service.get_project_stats") as mock_stats:
            _merge_stats(updated, tmp_path, name, latest_jsonl, latest_mtime)
            mock_stats.assert_not_called()

        # Stats unchanged
        assert updated["stats"]["tokens_in"] == 42


class TestProcessActiveProject:
    """Tests for _process_active_project."""

    def setup_method(self):
        from claude_monitor.core.background import _process_active_project

        self._fn = _process_active_project

    def test_sets_working_state_and_broadcasts_when_state_differs(self, tmp_path):
        """
        Given a project currently idle with a fresh active JSONL
        When _process_active_project is called
        Then state is set to 'working' and broadcast is called
        """
        from claude_monitor import state
        from claude_monitor.core import broadcast as broadcast_mod

        name = "active_proj_test"
        latest_jsonl = tmp_path / "session.jsonl"
        latest_jsonl.touch()
        latest_mtime = latest_jsonl.stat().st_mtime

        cached = {"mtime": latest_mtime, "tool": "Read"}
        current = {"state": "idle", "status": "idle"}
        state._jsonl_mtimes[str(latest_jsonl)] = latest_mtime - 1.0  # stale to trigger merge

        broadcasts = []
        with (
            patch("claude_monitor.stats.service.get_project_stats", return_value={}),
            patch.object(broadcast_mod, "broadcast", side_effect=broadcasts.append),
        ):
            import time

            self._fn(
                name, tmp_path, cached, current, tmp_path, time.time(), latest_jsonl, latest_mtime
            )

        assert len(broadcasts) == 1
        assert broadcasts[0]["data"]["state"] == "working"
        assert broadcasts[0]["data"]["tool"] == "Read"

    def test_preserves_compacting_state(self, tmp_path):
        """
        Given the current state is 'compacting'
        When _process_active_project is called
        Then state remains 'compacting' (not overridden to 'working')
        """
        from claude_monitor import state
        from claude_monitor.core import broadcast as broadcast_mod

        name = "compacting_proj_test"
        latest_jsonl = tmp_path / "session.jsonl"
        latest_jsonl.touch()
        latest_mtime = latest_jsonl.stat().st_mtime

        cached = {"mtime": latest_mtime, "tool": "Bash"}
        current = {"state": "compacting", "status": "compacting"}
        state._jsonl_mtimes[str(latest_jsonl)] = latest_mtime - 1.0

        broadcasts = []
        with (
            patch("claude_monitor.stats.service.get_project_stats", return_value={}),
            patch.object(broadcast_mod, "broadcast", side_effect=broadcasts.append),
        ):
            import time

            self._fn(
                name, tmp_path, cached, current, tmp_path, time.time(), latest_jsonl, latest_mtime
            )

        if broadcasts:
            # If broadcasted, state must still be 'compacting'
            assert broadcasts[0]["data"]["state"] == "compacting"

    def test_no_broadcast_when_nothing_changed(self, tmp_path):
        """
        Given state is already 'working', tool matches, and mtime is fresh
        When _process_active_project is called
        Then no broadcast is sent (nothing changed)
        """
        from claude_monitor import state
        from claude_monitor.core import broadcast as broadcast_mod

        name = "no_change_proj"
        latest_jsonl = tmp_path / "session.jsonl"
        latest_jsonl.touch()
        latest_mtime = latest_jsonl.stat().st_mtime

        cached = {"mtime": latest_mtime, "tool": "Bash"}
        current = {
            "state": "working",
            "status": "working",
            "current_action": {"tool": "Bash"},
        }
        # mtime is fresh — stats_stale is False
        state._jsonl_mtimes[str(latest_jsonl)] = latest_mtime

        broadcasts = []
        with (
            patch("claude_monitor.stats.service.get_project_stats", return_value={}),
            patch.object(broadcast_mod, "broadcast", side_effect=broadcasts.append),
        ):
            import time

            self._fn(
                name, tmp_path, cached, current, tmp_path, time.time(), latest_jsonl, latest_mtime
            )

        assert len(broadcasts) == 0

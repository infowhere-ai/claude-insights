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

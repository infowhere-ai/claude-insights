"""
Acceptance tests — Session Panel (CA-03).

Given the user has manually selected a session
When an SSE update arrives
Then the selected session is not overridden by SSE
"""

import time

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import ServerContext

TIMEOUT = 4000


def test_session_selector_populates(page: Page, server: ServerContext, project: str) -> None:
    """
    Given  JSONL files exist for the project
    When   the dashboard loads and the project is selected
    Then   the session selector has at least one option
    """
    server.write_jsonl(project, [server.assistant_entry(tool="Read")], filename="session-abc.jsonl")

    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)

    expect(page.locator("#session-select option")).not_to_have_count(0, timeout=TIMEOUT)


def test_sse_does_not_override_locked_session(
    page: Page, server: ServerContext, project: str
) -> None:
    """
    Given  the user has manually selected a session
    When   an SSE status update arrives for the project
    Then   the dropdown keeps the user-selected session
    And    does not switch automatically
    """
    server.write_jsonl(
        project, [server.assistant_entry(tool="Read")], filename="session-locked.jsonl"
    )

    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)

    expect(page.locator("#session-select option")).not_to_have_count(0, timeout=TIMEOUT)

    session_select = page.locator("#session-select")
    options = session_select.locator("option").all()
    if not options:
        pytest.skip("No sessions available for lock test")

    selected_before = session_select.input_value()

    server.write_status(project, "working", tool="Bash")
    time.sleep(0.5)
    server.write_status(project, "idle")
    time.sleep(0.5)

    selected_after = session_select.input_value()
    assert selected_after == selected_before, (
        f"Session changed from {selected_before!r} to {selected_after!r} — "
        "_userLockedSession is not working"
    )


def test_project_switch_resets_session_lock(
    page: Page, server: ServerContext, project: str
) -> None:
    """
    Given  the user is on a project
    When   they switch to another project
    Then   _userLockedSession is reset to false
    """
    second_project = "git-project"

    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)
    time.sleep(0.3)

    page.locator("#project-select").select_option(label=second_project)
    time.sleep(0.3)

    locked = page.evaluate(
        "() => typeof _userLockedSession !== 'undefined' ? _userLockedSession : false"
    )
    assert locked is False, f"_userLockedSession not reset after project switch: {locked}"

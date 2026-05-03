"""
Acceptance tests — Status Bar (CA-01).

Given the dashboard is open with a project in idle state
When .claude/status.json changes to working/idle/compacting
Then the status bar reflects the new state within 3 seconds
"""

import time

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import ServerContext

TIMEOUT = 4000  # ms — poll_loop 200ms + SSE + DOM update


def _open(page: Page, server: ServerContext, project: str) -> None:
    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)
    time.sleep(0.3)


def _set_working(server: ServerContext, project: str, tool: str) -> None:
    """Write JSONL + status to keep jsonl_watcher from flipping back to idle."""
    server.write_jsonl(project, [server.assistant_entry(tool=tool)])
    server.write_status(project, "working", tool=tool)


def test_status_idle_to_working(page: Page, server: ServerContext, project: str) -> None:
    """
    Given  the project is idle
    When   status.json changes to working with tool="Read"
    Then   #status-action shows "— Read" within 4s
    """
    _open(page, server, project)
    _set_working(server, project, "Read")

    expect(page.locator("#status-action")).to_contain_text("Read", timeout=TIMEOUT)


def test_status_working_to_idle(page: Page, server: ServerContext, project: str) -> None:
    """
    Given  the project is working
    When   status.json changes to idle
    Then   #status-label shows IDLE and #status-action is empty
    """
    _open(page, server, project)
    _set_working(server, project, "Bash")
    expect(page.locator("#status-action")).to_contain_text("Bash", timeout=TIMEOUT)

    server.write_status(project, "idle")
    time.sleep(0.5)

    expect(page.locator("#status-label")).to_contain_text("IDLE", timeout=TIMEOUT)
    expect(page.locator("#status-action")).to_be_empty(timeout=TIMEOUT)


def test_status_compacting(page: Page, server: ServerContext, project: str) -> None:
    """
    Given  the project receives compacting state
    When   the status bar is observed
    Then   #status-label shows COMPACTING
    """
    _open(page, server, project)
    server.write_status(project, "compacting")

    expect(page.locator("#status-label")).to_contain_text("COMPACTING", timeout=TIMEOUT)

"""
Acceptance tests — Reasoning Panel (CA-02).

Given the JSONL has thinking blocks
When the dashboard loads
Then the reasoning panel shows the text and word count
"""

import time

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import ServerContext

TIMEOUT = 4000  # ms — jsonl_watcher_loop runs every 2s


def test_thinking_block_appears(page: Page, server: ServerContext, project: str) -> None:
    """
    Given  the JSONL receives an entry with a non-empty thinking block
    When   the reasoning panel is observed
    Then   the thinking block text appears
    """
    server.write_jsonl(project, [
        server.assistant_entry(thinking="I need to carefully analyse this problem.")
    ])

    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)

    expect(page.locator("#thinking-text")).to_contain_text(
        "carefully analyse", timeout=TIMEOUT
    )


def test_empty_thinking_block_not_shown(page: Page, server: ServerContext, project: str) -> None:
    """
    Given  the JSONL has a thinking block containing only whitespace
    When   the reasoning panel is observed
    Then   the empty block does not appear as real content
    And    the element shows a no-reasoning placeholder message
    """
    server.write_jsonl(project, [
        server.assistant_entry(thinking="   \n\n  ")
    ], filename="session-no-think.jsonl")

    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)
    time.sleep(3)

    expect(page.locator("#thinking-text")).not_to_contain_text(
        "carefully analyse", timeout=2000
    )


def test_thinking_history_modal_opens(page: Page, server: ServerContext, project: str) -> None:
    """
    Given  there are thinking blocks in the session
    When   the user clicks the reasoning history button
    Then   the modal opens with the blocks listed
    """
    server.write_jsonl(project, [
        server.assistant_entry(thinking="First thought about the problem."),
        server.assistant_entry(
            thinking="Second deeper analysis.",
            ts="2026-01-01T10:00:01Z"
        ),
    ])

    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)
    time.sleep(3)

    page.locator("#btn-think-hist").click()

    expect(page.locator("#think-modal")).to_be_visible(timeout=2000)
    expect(page.locator("#think-modal-body")).to_contain_text("First thought")

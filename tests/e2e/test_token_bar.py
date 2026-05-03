"""
Acceptance tests — Token Bar (CA-05).

Given the JSONL has usage with known token counts
When the dashboard updates
Then token values and percentages are correct

Note: #pane-tokens starts with class hidden. Click #btn-tokens to open it.
"""

import time

from playwright.sync_api import Page, expect

from tests.e2e.conftest import ServerContext

TIMEOUT = 5000


def _open_tokens_pane(page: Page) -> None:
    """Open the token pane if it is hidden."""
    pane = page.locator("#pane-tokens")
    if "hidden" in (pane.get_attribute("class") or ""):
        page.locator("#btn-tokens").click()


def test_ctx_tokens_shown(page: Page, server: ServerContext, project: str) -> None:
    """
    Given  the JSONL has input=100, cache_read=200, cache_creation=0
    When   the token pane is opened
    Then   #tok-ctx shows 300 (input + cache_read + cache_creation)
    """
    server.write_jsonl(project, [
        server.assistant_entry(
            tool="Read",
            input_tokens=100,
            output_tokens=50,
            cache_read=200,
            cache_creation=0,
        )
    ], filename="session-ctx.jsonl", newest=True)
    server.write_status(project, "working", tool="Read")

    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)
    time.sleep(1)

    _open_tokens_pane(page)

    expect(page.locator("#tok-ctx")).to_contain_text("300", timeout=TIMEOUT)


def test_cache_tokens_shown(page: Page, server: ServerContext, project: str) -> None:
    """
    Given  the JSONL has cache_read_input_tokens=200
    When   the token pane is observed
    Then   #tok-cache shows 200
    """
    server.write_jsonl(project, [
        server.assistant_entry(
            tool="Read",
            input_tokens=50,
            output_tokens=30,
            cache_read=200,
        )
    ], filename="session-cache.jsonl", newest=True)

    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)
    time.sleep(1)

    _open_tokens_pane(page)

    expect(page.locator("#tok-cache")).to_contain_text("200", timeout=TIMEOUT)

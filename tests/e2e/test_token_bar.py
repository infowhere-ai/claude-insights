"""
Acceptance tests — Token Bar (CA-05).

Dado que o JSONL tem usage com tokens conhecidos
Quando o dashboard actualiza
Então os tokens e percentagens são correctos

Nota: #pane-tokens começa com classe hidden (btn-tokens não é active por defeito
nesta versão). Clicar #btn-tokens abre o painel.
"""

import time

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import ServerContext

TIMEOUT = 5000


def _open_tokens_pane(page: Page) -> None:
    """Open token pane if it's hidden."""
    pane = page.locator("#pane-tokens")
    if "hidden" in (pane.get_attribute("class") or ""):
        page.locator("#btn-tokens").click()


def test_ctx_tokens_shown(page: Page, server: ServerContext, project: str) -> None:
    """
    Dado que   o JSONL tem input=100, cache_read=200, cache_creation=0
    Quando     o painel de tokens é aberto
    Então      #tok-ctx mostra 300 (input + cache_read + cache_creation)
    """
    # Use unique filename so this is always the newest JSONL (avoids stale cache)
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
    Dado que   o JSONL tem cache_read_input_tokens=200
    Quando     o painel de tokens é observado
    Então      #tok-cache mostra 200
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

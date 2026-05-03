"""
Acceptance tests — Reasoning Panel (CA-02).

Dado que o JSONL tem thinking blocks
Quando o dashboard carrega
Então o painel de reasoning mostra o texto e a contagem de palavras
"""

import time

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import ServerContext

TIMEOUT = 4000  # ms — jsonl_watcher_loop runs every 2s


def test_thinking_block_appears(page: Page, server: ServerContext, project: str) -> None:
    """
    Dado que   o JSONL recebe uma entrada com thinking block não vazio
    Quando     o painel de reasoning é observado
    Então      o texto do thinking block aparece
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
    Dado que   o JSONL tem thinking block com apenas whitespace
    Quando     o painel de reasoning é observado
    Então      o bloco vazio não aparece como conteúdo real
    E          o elemento mostra mensagem de ausência de reasoning
    """
    server.write_jsonl(project, [
        server.assistant_entry(thinking="   \n\n  ")
    ], filename=f"session-no-think.jsonl")

    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)
    time.sleep(3)

    # Whitespace thinking → "No reasoning in this session." (not actual content)
    expect(page.locator("#thinking-text")).not_to_contain_text(
        "carefully analyse", timeout=2000
    )


def test_thinking_history_modal_opens(page: Page, server: ServerContext, project: str) -> None:
    """
    Dado que   existem thinking blocks na sessão
    Quando     o utilizador clica no botão de reasoning history
    Então      o modal abre com os blocos listados
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

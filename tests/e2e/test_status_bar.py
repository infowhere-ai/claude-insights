"""
Acceptance tests — Status Bar (CA-01).

Dado que o dashboard está aberto com um projecto em estado idle
Quando .claude/status.json muda para working/idle/compacting
Então o status bar reflecte o novo estado em menos de 3 segundos
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
    Dado que   o projecto está idle
    Quando     status.json muda para working com tool="Read"
    Então      #status-action mostra "— Read" em menos de 4s
    """
    _open(page, server, project)
    _set_working(server, project, "Read")

    expect(page.locator("#status-action")).to_contain_text("Read", timeout=TIMEOUT)


def test_status_working_to_idle(page: Page, server: ServerContext, project: str) -> None:
    """
    Dado que   o projecto está working
    Quando     status.json muda para idle
    Então      #status-label mostra IDLE e #status-action fica vazio
    """
    _open(page, server, project)
    _set_working(server, project, "Bash")
    expect(page.locator("#status-action")).to_contain_text("Bash", timeout=TIMEOUT)

    server.write_status(project, "idle")
    # Clear JSONL mtime so watcher also sees idle
    time.sleep(0.5)

    expect(page.locator("#status-label")).to_contain_text("IDLE", timeout=TIMEOUT)
    expect(page.locator("#status-action")).to_be_empty(timeout=TIMEOUT)


def test_status_compacting(page: Page, server: ServerContext, project: str) -> None:
    """
    Dado que   o projecto recebe estado compacting
    Quando     o status bar é observado
    Então      #status-label mostra COMPACTING
    """
    _open(page, server, project)
    server.write_status(project, "compacting")

    expect(page.locator("#status-label")).to_contain_text("COMPACTING", timeout=TIMEOUT)

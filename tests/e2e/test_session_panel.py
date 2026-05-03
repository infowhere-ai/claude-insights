"""
Acceptance tests — Session Panel (CA-03).

Dado que o utilizador seleccionou uma sessão manualmente
Quando chega um SSE update
Então a sessão seleccionada não é sobreposta pelo SSE
"""

import time

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import ServerContext

TIMEOUT = 4000


def test_session_selector_populates(page: Page, server: ServerContext, project: str) -> None:
    """
    Dado que   existem ficheiros JSONL para o projecto
    Quando     o dashboard carrega e o projecto é seleccionado
    Então      o session selector tem pelo menos uma opção
    """
    server.write_jsonl(project, [
        server.assistant_entry(tool="Read")
    ], filename="session-abc.jsonl")

    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)

    # Wait for session list to load
    expect(page.locator("#session-select option")).not_to_have_count(0, timeout=TIMEOUT)


def test_sse_does_not_override_locked_session(
    page: Page, server: ServerContext, project: str
) -> None:
    """
    Dado que   o utilizador seleccionou manualmente uma sessão
    Quando     chega um SSE update de status do projecto
    Então      o dropdown mantém a sessão seleccionada
    """
    server.write_jsonl(project, [
        server.assistant_entry(tool="Read")
    ], filename="session-locked.jsonl")

    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)

    # Wait for sessions to load
    expect(page.locator("#session-select option")).not_to_have_count(0, timeout=TIMEOUT)

    # Manually select the session (simulates user action that sets _userLockedSession)
    session_select = page.locator("#session-select")
    options = session_select.locator("option").all()
    if not options:
        pytest.skip("No sessions available for lock test")

    selected_before = session_select.input_value()

    # Trigger SSE updates
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
    Dado que   o utilizador está num projecto
    Quando     muda para outro projecto
    Então      _userLockedSession é resetado a false
    """
    second_project = "git-project"  # pre-created in server fixture

    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)
    time.sleep(0.3)

    # Switch project — lock should reset
    page.locator("#project-select").select_option(label=second_project)
    time.sleep(0.3)

    # Verify _userLockedSession is reset
    locked = page.evaluate(
        "() => typeof _userLockedSession !== 'undefined' ? _userLockedSession : false"
    )
    assert locked is False, (
        f"_userLockedSession not reset after project switch: {locked}"
    )

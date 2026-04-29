"""Tests covering frontend bug fixes 7–10 in insights.html.

Each test fetches /insights and inspects the returned HTML for the presence
(or absence) of specific markers that prove the fix is in place.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── Bug 7 — active_agents panel must exist in the HTML ────────────────────────

def test_bug7_agents_panel_present(app_client):
    """The active_agents panel div must be in insights.html."""
    r = app_client.get("/insights")
    assert r.status_code == 200
    html = r.text
    assert 'id="agents-panel"' in html, (
        "Bug 7: #agents-panel element missing — active_agents are never rendered"
    )


def test_bug7_agents_list_present(app_client):
    """The agents-list div (populated by renderActiveAgents) must be in insights.html."""
    r = app_client.get("/insights")
    assert r.status_code == 200
    html = r.text
    assert 'id="agents-list"' in html, (
        "Bug 7: #agents-list element missing — renderActiveAgents has nowhere to write"
    )


def test_bug7_render_function_present(app_client):
    """The renderActiveAgents JS function must be defined in insights.html."""
    r = app_client.get("/insights")
    assert r.status_code == 200
    html = r.text
    assert "renderActiveAgents" in html, (
        "Bug 7: renderActiveAgents function missing from insights.html"
    )


def test_bug7_render_called_on_sse_update(app_client):
    """renderActiveAgents must be called inside the SSE update handler."""
    r = app_client.get("/insights")
    assert r.status_code == 200
    html = r.text
    # The call site passes active_agents from msg.data
    assert "renderActiveAgents(msg.data" in html, (
        "Bug 7: renderActiveAgents not called in SSE update handler"
    )


# ── Bug 8 — userLockedSession flag prevents SSE overriding manual selection ───

def test_bug8_locked_session_flag_declared(app_client):
    """The _userLockedSession flag must be declared as a JS variable."""
    r = app_client.get("/insights")
    assert r.status_code == 200
    html = r.text
    assert "_userLockedSession" in html, (
        "Bug 8: _userLockedSession flag not declared in insights.html"
    )


def test_bug8_lock_set_on_session_change(app_client):
    """onSessionSelectChange must set _userLockedSession."""
    r = app_client.get("/insights")
    assert r.status_code == 200
    html = r.text
    assert "_userLockedSession = !active" in html or "_userLockedSession = true" in html, (
        "Bug 8: _userLockedSession not set in onSessionSelectChange"
    )


def test_bug8_sse_handler_checks_lock(app_client):
    """The SSE loadSessions() call must check !_userLockedSession."""
    r = app_client.get("/insights")
    assert r.status_code == 200
    html = r.text
    assert "!_userLockedSession" in html, (
        "Bug 8: SSE handler does not check _userLockedSession before calling loadSessions()"
    )


def test_bug8_lock_reset_on_project_switch(app_client):
    """switchProject must reset _userLockedSession to false."""
    r = app_client.get("/insights")
    assert r.status_code == 200
    html = r.text
    assert "_userLockedSession = false" in html, (
        "Bug 8: _userLockedSession not reset in switchProject"
    )


# ── Bug 9 — unreleased GitHub repo link must not be in the About dialog ───────

def test_bug9_github_link_removed(app_client):
    """The href to the unreleased github.com/infowhere-ai/claude-insights must be gone."""
    r = app_client.get("/insights")
    assert r.status_code == 200
    html = r.text
    assert "https://github.com/infowhere-ai/claude-insights" not in html, (
        "Bug 9: unreleased GitHub link still present in About dialog — users get a 404"
    )


def test_bug9_about_dialog_still_renders(app_client):
    """The About dialog markup must still be present after removing the link."""
    r = app_client.get("/insights")
    assert r.status_code == 200
    html = r.text
    assert 'id="about-modal"' in html, (
        "Bug 9: About dialog removed entirely — only the link should be gone"
    )


# ── Bug 10 — mobile media queries must be present ────────────────────────────

def test_bug10_media_query_present(app_client):
    """insights.html must contain at least one @media rule for small viewports."""
    r = app_client.get("/insights")
    assert r.status_code == 200
    html = r.text
    assert "@media" in html, (
        "Bug 10: no @media CSS rules found — layout breaks on mobile"
    )


def test_bug10_mobile_breakpoint_768(app_client):
    """The 768px breakpoint must be defined."""
    r = app_client.get("/insights")
    assert r.status_code == 200
    html = r.text
    assert "max-width: 768px" in html or "max-width:768px" in html, (
        "Bug 10: 768px breakpoint missing from media queries"
    )


def test_bug10_columns_stack_on_mobile(app_client):
    """Mobile CSS must include flex-direction: column for the main layout."""
    r = app_client.get("/insights")
    assert r.status_code == 200
    html = r.text
    assert "flex-direction: column" in html, (
        "Bug 10: flex-direction: column missing — columns do not stack on mobile"
    )

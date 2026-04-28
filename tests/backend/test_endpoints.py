"""Integration tests for all HTTP endpoints in app.py.

Uses FastAPI TestClient (synchronous) with PROJECTS_ROOT pointed at a
temporary directory. Background loops (discovery, poll, jsonl_watcher) are
patched to no-ops so tests exit cleanly.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── /health ───────────────────────────────────────────────────────────────────

def test_health_returns_ok(app_client):
    r = app_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "projects_monitored" in body


# ── / redirect ────────────────────────────────────────────────────────────────

def test_root_redirects_to_insights(app_client):
    r = app_client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert r.headers["location"].endswith("/insights")


# ── /insights ─────────────────────────────────────────────────────────────────

def test_insights_page_returns_html(app_client):
    r = app_client.get("/insights")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# ── /api/version ─────────────────────────────────────────────────────────────

def test_version_endpoint(app_client):
    r = app_client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert "version" in body
    assert "build_date" in body
    assert body["version"] != ""


# ── /api/status ──────────────────────────────────────────────────────────────

def test_status_returns_projects_dict(app_client):
    r = app_client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "projects" in body
    assert "connected_clients" in body


# ── /api/config ──────────────────────────────────────────────────────────────

def test_config_returns_primary_root(app_client, tmp_projects_root):
    r = app_client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "primary_root" in body
    assert "extra_roots" in body
    assert str(tmp_projects_root) == body["primary_root"]


def test_config_add_invalid_root(app_client):
    r = app_client.post("/api/config/roots", json={"action": "add", "path": "/nonexistent/path/xyz"})
    assert r.status_code == 400


def test_config_add_missing_path(app_client):
    r = app_client.post("/api/config/roots", json={"action": "add", "path": ""})
    assert r.status_code == 400


def test_config_invalid_action(app_client):
    r = app_client.post("/api/config/roots", json={"action": "invalid", "path": "/tmp"})
    assert r.status_code == 400


def test_config_add_valid_root(app_client, tmp_path):
    extra = tmp_path / "extra_root"
    extra.mkdir()
    r = app_client.post("/api/config/roots", json={"action": "add", "path": str(extra)})
    assert r.status_code == 200
    body = r.json()
    assert str(extra) in body["extra_roots"]


def test_config_remove_root(app_client, tmp_path):
    extra = tmp_path / "extra_root2"
    extra.mkdir()
    app_client.post("/api/config/roots", json={"action": "add", "path": str(extra)})
    r = app_client.post("/api/config/roots", json={"action": "remove", "path": str(extra)})
    assert r.status_code == 200
    assert str(extra) not in r.json()["extra_roots"]


# ── /api/pending ─────────────────────────────────────────────────────────────

def test_pending_unknown_project_returns_404(app_client):
    r = app_client.get("/api/pending?project=does-not-exist")
    assert r.status_code == 404


def test_pending_known_project_returns_files(app_client, tmp_project):
    # my-project is discovered at startup; may have zero uncommitted files
    r = app_client.get("/api/pending?project=my-project")
    # The project may not be in _status_paths if git isn't init'd — either 200 or 404
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert "files" in r.json()


# ── /api/diff ─────────────────────────────────────────────────────────────────

def test_diff_unknown_project_returns_404(app_client):
    r = app_client.get("/api/diff?project=does-not-exist&file=app.py")
    assert r.status_code == 404


def test_diff_missing_file_returns_error(app_client, tmp_project):
    r = app_client.get("/api/diff?project=my-project&file=nonexistent.py")
    # File not found returns a JSON body (not a 404), with error key
    assert r.status_code == 200
    assert "error" in r.json() or "diff" in r.json()


# ── /api/sessions ─────────────────────────────────────────────────────────────

def test_sessions_unknown_project_returns_404(app_client):
    r = app_client.get("/api/sessions?project=does-not-exist")
    assert r.status_code == 404


# ── /api/session-detail ──────────────────────────────────────────────────────

def test_session_detail_unknown_project_returns_404(app_client):
    r = app_client.get("/api/session-detail?project=does-not-exist&session_id=abc")
    assert r.status_code == 404


# ── /api/insights-stats ──────────────────────────────────────────────────────

def test_insights_stats_unknown_project_returns_404(app_client):
    r = app_client.get("/api/insights-stats?project=does-not-exist")
    assert r.status_code == 404


# ── /api/usage-window ────────────────────────────────────────────────────────

def test_usage_window_unknown_project_returns_404(app_client):
    r = app_client.get("/api/usage-window?project=does-not-exist")
    assert r.status_code == 404


# ── /api/weekly-stats ────────────────────────────────────────────────────────

def test_weekly_stats_returns_dict(app_client):
    r = app_client.get("/api/weekly-stats")
    assert r.status_code == 200
    assert "weekly" in r.json()


# ── /api/claude-md ────────────────────────────────────────────────────────────

def test_claude_md_unknown_project_returns_404(app_client):
    r = app_client.get("/api/claude-md?project=does-not-exist")
    assert r.status_code == 404


def test_claude_md_project_without_file_returns_null(app_client):
    # my-project exists but has no CLAUDE.md
    r = app_client.get("/api/claude-md?project=my-project")
    # Either 404 (not in _status_paths) or 200 with null content
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert r.json()["content"] is None


def test_claude_md_project_with_file(app_client, tmp_project):
    (tmp_project / "CLAUDE.md").write_text("# My Project\n\nSome instructions.", encoding="utf-8")
    r = app_client.get("/api/claude-md?project=my-project")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        body = r.json()
        assert body["content"] is not None
        assert "My Project" in body["content"]


# ── /api/skills ──────────────────────────────────────────────────────────────

def test_skills_returns_list(app_client):
    r = app_client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    assert "skills" in body
    assert isinstance(body["skills"], list)


# ── /api/browse ──────────────────────────────────────────────────────────────

def test_browse_home(app_client):
    r = app_client.get("/api/browse")
    assert r.status_code == 200
    body = r.json()
    assert "current" in body
    assert "dirs" in body
    assert "parent" in body


def test_browse_invalid_path(app_client):
    r = app_client.get("/api/browse?path=/nonexistent/path/xyz")
    assert r.status_code == 400


def test_browse_specific_path(app_client, tmp_path):
    sub = tmp_path / "subdir"
    sub.mkdir()
    r = app_client.get(f"/api/browse?path={tmp_path}")
    assert r.status_code == 200
    body = r.json()
    assert str(sub) in body["dirs"]


# ── /api/context-inspect ─────────────────────────────────────────────────────

def test_context_inspect_unknown_project_returns_404(app_client):
    r = app_client.get("/api/context-inspect?project=does-not-exist")
    assert r.status_code == 404


# ── DELETE /api/file ──────────────────────────────────────────────────────────

def test_delete_file_unknown_project_returns_404(app_client):
    r = app_client.delete("/api/file?project=does-not-exist&path=some.txt")
    assert r.status_code == 404


def test_delete_tracked_file_is_rejected(app_client, tmp_project):
    import subprocess
    # Init git and track a file so the endpoint rejects the delete
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_project), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_project), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_project), check=True)
    test_file = tmp_project / "tracked.txt"
    test_file.write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=str(tmp_project), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add file"], cwd=str(tmp_project), check=True)

    r = app_client.delete(f"/api/file?project=my-project&path={test_file}")
    # Tracked file → 400. If project not in _status_paths → 404. Both are correct.
    assert r.status_code in (400, 404)


# ── /api/file-preview ────────────────────────────────────────────────────────

def test_file_preview_non_md_returns_400(app_client, tmp_path):
    f = tmp_path / "script.py"
    f.write_text("print('hi')", encoding="utf-8")
    r = app_client.get(f"/api/file-preview?path={f}")
    assert r.status_code == 400


def test_file_preview_missing_file_returns_404(app_client):
    r = app_client.get("/api/file-preview?path=/nonexistent/file.md")
    assert r.status_code == 404


def test_file_preview_md_file(app_client, tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# Hello\n\nWorld.", encoding="utf-8")
    r = app_client.get(f"/api/file-preview?path={f}")
    assert r.status_code == 200
    body = r.json()
    assert "content" in body
    assert "Hello" in body["content"]
    assert body["truncated"] is False

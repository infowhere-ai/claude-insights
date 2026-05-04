"""Tests for configuration endpoints."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def test_config_returns_primary_root(app_client, tmp_projects_root):
    r = app_client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "primary_root" in body
    assert "extra_roots" in body
    assert str(tmp_projects_root) == body["primary_root"]


def test_config_add_invalid_root(app_client):
    r = app_client.post(
        "/api/config/roots", json={"action": "add", "path": "/nonexistent/path/xyz"}
    )
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


def test_add_primary_root_as_extra_is_rejected(app_client, tmp_projects_root):
    r = app_client.post("/api/config/roots", json={"action": "add", "path": str(tmp_projects_root)})
    assert r.status_code == 400
    assert "already the primary" in r.json()["error"]


def test_claude_md_unknown_project_returns_404(app_client):
    r = app_client.get("/api/claude-md?project=does-not-exist")
    assert r.status_code == 404


def test_claude_md_project_without_file_returns_null(app_client):
    r = app_client.get("/api/claude-md?project=my-project")
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


def test_claude_md_found_in_extra_root(app_client, tmp_path):
    from claude_monitor import state

    extra_root = tmp_path / "extra"
    project = extra_root / "extra-project"
    project.mkdir(parents=True)
    (project / "CLAUDE.md").write_text("# Extra Project\n\nContent here.")

    original_extras = list(state._extra_roots)
    state._extra_roots.append(extra_root)
    try:
        r = app_client.get("/api/claude-md?project=extra-project")
        assert r.status_code == 200
        assert "Extra Project" in r.json()["content"]
    finally:
        state._extra_roots[:] = original_extras

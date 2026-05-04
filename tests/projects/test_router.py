"""Tests for project status endpoints and basic pages."""


def test_health_returns_ok(app_client):
    r = app_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "projects_monitored" in body


def test_root_redirects_to_insights(app_client):
    r = app_client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert r.headers["location"].endswith("/insights")


def test_insights_page_returns_html(app_client):
    r = app_client.get("/insights")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_version_endpoint(app_client):
    r = app_client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert "version" in body
    assert "build_date" in body
    assert body["version"] != ""


def test_status_returns_projects_dict(app_client):
    r = app_client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "projects" in body
    assert "connected_clients" in body


def test_status_project_has_active_agents_key(app_client):
    r = app_client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    for name, data in body.get("projects", {}).items():
        assert "active_agents" in data
        assert isinstance(data["active_agents"], list)


def test_status_active_agents_reads_agent_files(app_client, tmp_project):
    import json as _json

    agents_dir = tmp_project / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    agent = {
        "id": "agent_abc123",
        "state": "running",
        "started_at": "2026-01-01T10:00:00Z",
        "last_updated": "2026-01-01T10:00:00Z",
        "description": "Write tests for the auth module",
    }
    (agents_dir / "agent_abc123.json").write_text(_json.dumps(agent), encoding="utf-8")
    r = app_client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    for data in body.get("projects", {}).values():
        assert "active_agents" in data

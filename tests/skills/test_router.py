"""Tests for skills endpoint."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def test_skills_returns_list(app_client):
    r = app_client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    assert "skills" in body
    assert isinstance(body["skills"], list)


def test_skills_with_skill_md_file(app_client, tmp_path):
    from claude_monitor import config

    skills_dir = tmp_path / "skills" / "my-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: My Skill\ndescription: Does things\n---\n\nBody text."
    )
    with patch.object(config, "CLAUDE_SKILLS_DIR", tmp_path / "skills"):
        r = app_client.get("/api/skills")
    assert r.status_code == 200
    skills = r.json()["skills"]
    assert any(s["name"] == "my-skill" for s in skills)


def test_skills_handles_unreadable_file(app_client, tmp_path):
    skills_dir = tmp_path / ".claude" / "skills" / "broken-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("content")
    from claude_monitor import config
    from claude_monitor.skills import service as skill_service

    with (
        patch.object(config, "CLAUDE_SKILLS_DIR", tmp_path / "skills"),
        patch.object(skill_service, "parse_skill_md", side_effect=Exception("parse error")),
    ):
        r = app_client.get("/api/skills")
    assert r.status_code == 200

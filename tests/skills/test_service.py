"""Unit tests for skill parsing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from claude_monitor.skills import service


class TestParseSkillMd:
    def test_full_frontmatter(self):
        content = """---
name: my-skill
description: Does something useful
argument-hint: <project-name>
---

## How it works

Some body text here.
"""
        result = service.parse_skill_md(content, "my-skill")
        assert result["title"] == "my-skill"
        assert result["description"] == "Does something useful"
        assert result["argument_hint"] == "<project-name>"

    def test_no_frontmatter_uses_heading(self):
        content = "# My Skill Title\n\nSome body text.\n"
        result = service.parse_skill_md(content, "my-skill")
        assert result["title"] == "My Skill Title"
        assert result["description"] == ""

    def test_body_intro_extracted(self):
        content = (
            "---\nname: test\ndescription: desc\n---\n\nFirst paragraph.\n\nSecond paragraph.\n"
        )
        result = service.parse_skill_md(content, "test")
        assert "First paragraph" in result["body_intro"]
        assert "Second paragraph" not in result["body_intro"]

    def test_empty_content(self):
        result = service.parse_skill_md("", "fallback-name")
        assert result["name"] == "fallback-name"
        assert result["title"] == "fallback-name"
        assert result["description"] == ""

    def test_name_field_in_result(self):
        result = service.parse_skill_md("", "skill-xyz")
        assert result["name"] == "skill-xyz"

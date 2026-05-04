"""Unit tests for skill parsing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from claude_monitor.skills import service


class TestParseFrontmatter:
    def test_extracts_name_description_and_hint(self):
        """_parse_frontmatter returns dict from YAML-like frontmatter and body start index."""
        lines = [
            "---",
            "name: my-skill",
            "description: Does something",
            "argument-hint: <arg>",
            "---",
            "",
            "Body text.",
        ]
        fm, body_start = service._parse_frontmatter(lines)
        assert fm["name"] == "my-skill"
        assert fm["description"] == "Does something"
        assert fm["argument-hint"] == "<arg>"
        assert body_start == 5

    def test_returns_empty_dict_when_no_frontmatter(self):
        """_parse_frontmatter returns ({}, 0) when content has no --- delimiters."""
        lines = ["# Title", "", "Some text."]
        fm, body_start = service._parse_frontmatter(lines)
        assert fm == {}
        assert body_start == 0

    def test_returns_empty_dict_when_no_closing_delimiter(self):
        """_parse_frontmatter returns ({}, 0) when closing --- is missing."""
        lines = ["---", "name: partial", "no closing delimiter"]
        fm, body_start = service._parse_frontmatter(lines)
        assert fm == {}
        assert body_start == 0

    def test_ignores_lines_without_colon(self):
        """_parse_frontmatter skips frontmatter lines that have no colon."""
        lines = ["---", "name: valid", "no-colon-here", "---", "Body."]
        fm, _ = service._parse_frontmatter(lines)
        assert "name" in fm
        assert "no-colon-here" not in fm

    def test_empty_lines_list(self):
        """_parse_frontmatter returns ({}, 0) for empty input."""
        fm, body_start = service._parse_frontmatter([])
        assert fm == {}
        assert body_start == 0


class TestExtractBodyIntro:
    def test_returns_first_non_header_paragraph(self):
        """_extract_body_intro returns the first paragraph text, skipping headings."""
        lines = [
            "## How it works",
            "",
            "This is the intro paragraph.",
            "",
            "This is second paragraph.",
        ]
        result = service._extract_body_intro(lines, 0)
        assert "This is the intro paragraph." in result
        assert "second paragraph" not in result

    def test_skips_separator_lines(self):
        """_extract_body_intro skips --- separator lines."""
        lines = ["---", "Actual intro.", "More intro.", ""]
        result = service._extract_body_intro(lines, 0)
        assert "Actual intro." in result

    def test_returns_empty_string_when_no_body(self):
        """_extract_body_intro returns empty string when no non-heading text found."""
        lines = ["## Heading only"]
        result = service._extract_body_intro(lines, 0)
        assert result == ""

    def test_respects_body_start_offset(self):
        """_extract_body_intro starts scanning from body_start index."""
        lines = ["# Ignored", "Also ignored", "## Start here", "Real intro."]
        result = service._extract_body_intro(lines, 2)
        assert "Real intro." in result
        assert "Ignored" not in result

    def test_truncates_at_300_chars(self):
        """_extract_body_intro truncates result to 300 characters."""
        long_line = "x" * 400
        lines = [long_line]
        result = service._extract_body_intro(lines, 0)
        assert len(result) <= 300


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

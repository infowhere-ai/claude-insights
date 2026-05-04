"""Skill discovery and SKILL.md parsing."""


def _parse_frontmatter(lines: list[str]) -> tuple[dict, int]:
    """Parse YAML-like frontmatter from lines.

    Returns (frontmatter_dict, body_start_index).
    body_start_index is 0 when no frontmatter is found.
    """
    if not lines or lines[0].strip() != "---":
        return {}, 0
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        return {}, 0
    frontmatter: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            k, _, v = line.partition(":")
            frontmatter[k.strip()] = v.strip()
    return frontmatter, end + 1


def _extract_body_intro(lines: list[str], body_start: int) -> str:
    """Find the first non-header, non-empty paragraph starting from body_start.

    Returns the paragraph joined as a single string, truncated to 300 chars.
    """
    body_lines: list[str] = []
    collecting = False
    for line in lines[body_start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            if collecting:
                break
            continue
        if stripped.startswith("---"):
            continue
        if stripped:
            collecting = True
            body_lines.append(stripped)
        elif collecting:
            break
    return " ".join(body_lines)[:300]


def parse_skill_md(content: str, name: str) -> dict:
    lines = content.splitlines()
    frontmatter, body_start = _parse_frontmatter(lines)

    title = frontmatter.get("name", name)
    description = frontmatter.get("description", "")
    argument_hint = frontmatter.get("argument-hint", "")

    body_intro = _extract_body_intro(lines, body_start)

    if title == name:
        for line in lines[body_start:]:
            if line.strip().startswith("# "):
                title = line.strip()[2:].strip()
                break

    return {
        "name": name,
        "title": title,
        "description": description,
        "argument_hint": argument_hint,
        "body_intro": body_intro,
    }

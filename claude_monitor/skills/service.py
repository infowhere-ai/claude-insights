"""Skill discovery and SKILL.md parsing."""


def parse_skill_md(content: str, name: str) -> dict:
    lines = content.splitlines()
    frontmatter: dict[str, str] = {}
    body_start = 0

    if lines and lines[0].strip() == "---":
        end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), None)
        if end:
            for l in lines[1:end]:
                if ":" in l:
                    k, _, v = l.partition(":")
                    frontmatter[k.strip()] = v.strip()
            body_start = end + 1

    title = frontmatter.get("name", name)
    description = frontmatter.get("description", "")
    argument_hint = frontmatter.get("argument-hint", "")

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

    body_intro = " ".join(body_lines)[:300]

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

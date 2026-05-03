"""Skills endpoint."""
from fastapi import APIRouter

from claude_monitor import config
from claude_monitor.skills import service

router = APIRouter(tags=["skills"])


@router.get("/api/skills")
async def get_skills():
    skills = []
    search_dirs = [(config.CLAUDE_SKILLS_DIR, "user")]
    for base, source in search_dirs:
        if not base.is_dir():
            continue
        for skill_md in base.glob("*/SKILL.md"):
            name = skill_md.parent.name
            try:
                content = skill_md.read_text(encoding="utf-8")
                parsed = service.parse_skill_md(content, name)
                skills.append({**parsed, "source": source, "path": str(skill_md)})
            except Exception:
                skills.append({
                    "name": name, "title": name, "description": "",
                    "argument_hint": "", "body_intro": "",
                    "source": source, "path": str(skill_md),
                })
    skills.sort(key=lambda s: (s["source"], s["name"]))
    return {"skills": skills}

"""Context inspector endpoint."""
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from claude_monitor import config, state
from claude_monitor.jsonl import parser

router = APIRouter(tags=["context"])


@router.get("/api/context-inspect")
async def get_context_inspect(project: str = Query(...), session_id: str = Query(default="")):
    if project not in state._status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    project_path = state._status_paths[project].parents[1]

    rules: list[dict] = []

    def _add_rule(label: str, real_path: Path, category: str) -> None:
        try:
            size = real_path.stat().st_size
            rules.append({
                "label": label,
                "real_path": str(real_path),
                "size_bytes": size,
                "tokens_est": size // 4,
                "category": category,
            })
        except OSError:
            pass

    for candidate in [project_path / "CLAUDE.md", project_path / ".claude" / "CLAUDE.md"]:
        if candidate.is_file():
            _add_rule(candidate.name, candidate, "claude-md")

    global_claude = Path.home() / ".claude" / "CLAUDE.md"
    if global_claude.is_file():
        _add_rule("~/.claude/CLAUDE.md", global_claude, "global")

    rules_dir = project_path / ".claude" / "rules"
    if rules_dir.is_dir():
        for entry in sorted(rules_dir.iterdir()):
            try:
                real = entry.resolve()
                if real.is_file():
                    label = entry.name
                    try:
                        label = str(real.relative_to(config.PROJECTS_ROOT))
                    except ValueError:
                        pass
                    _add_rule(label, real, "rule")
                elif real.is_dir():
                    for md_file in sorted(real.rglob("*.md")):
                        if md_file.is_file():
                            label = md_file.name
                            try:
                                label = str(md_file.relative_to(config.PROJECTS_ROOT))
                            except ValueError:
                                pass
                            _add_rule(label, md_file, "rule")
            except OSError:
                pass

    global_rules = Path.home() / ".claude" / "rules"
    if global_rules.is_dir():
        for entry in sorted(global_rules.iterdir()):
            try:
                real = entry.resolve()
                if real.is_file():
                    _add_rule(f"~/.claude/rules/{entry.name}", real, "global-rule")
                elif real.is_dir():
                    for md_file in sorted(real.rglob("*.md")):
                        if md_file.is_file():
                            _add_rule(f"~/.claude/rules/{entry.name}/{md_file.name}", md_file, "global-rule")
            except OSError:
                pass

    rules.sort(key=lambda r: r["size_bytes"], reverse=True)
    rules_total_bytes = sum(r["size_bytes"] for r in rules)

    reads: list[dict] = []
    encoded = str(project_path).replace("/", "-")
    jsonl_dir = config.CLAUDE_PROJECTS_DIR / encoded

    jsonl_path: Path | None = None
    if session_id:
        candidate = jsonl_dir / f"{session_id}.jsonl"
        if candidate.is_file():
            jsonl_path = candidate
    if jsonl_path is None:
        latest, _ = parser.get_latest_jsonl(project_path)
        jsonl_path = latest

    if jsonl_path and jsonl_path.is_file():
        import json as _json
        tool_uses: dict[str, dict] = {}
        try:
            with jsonl_path.open(encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = _json.loads(line)
                    except Exception:
                        continue
                    if d.get("type") == "assistant":
                        for c in (d.get("message", {}).get("content", []) or []):
                            if isinstance(c, dict) and c.get("type") == "tool_use":
                                tool_uses[c["id"]] = {
                                    "name": c.get("name", ""),
                                    "input": c.get("input", {}),
                                }
                    elif d.get("type") == "user":
                        for c in (d.get("message", {}).get("content", []) or []):
                            if not isinstance(c, dict) or c.get("type") != "tool_result":
                                continue
                            tid = c.get("tool_use_id", "")
                            tu = tool_uses.get(tid, {})
                            tool_name = tu.get("name", "")
                            if tool_name not in ("Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch"):
                                continue
                            result_content = c.get("content", "")
                            if isinstance(result_content, list):
                                result_text = "\n".join(
                                    x.get("text", "") for x in result_content
                                    if isinstance(x, dict)
                                )
                            else:
                                result_text = str(result_content)
                            size_bytes = len(result_text.encode("utf-8"))
                            inp = tu.get("input", {})
                            label = (
                                inp.get("file_path") or inp.get("path") or
                                inp.get("command", "")[:60] or
                                inp.get("url") or inp.get("query") or
                                inp.get("pattern") or ""
                            )
                            reads.append({
                                "tool": tool_name,
                                "label": label,
                                "size_bytes": size_bytes,
                                "tokens_est": size_bytes // 4,
                                "is_error": c.get("is_error", False),
                                "content": result_text[:8000],
                                "total_chars": len(result_text),
                            })
        except OSError:
            pass

    last_pos: dict[tuple, int] = {}
    items: dict[tuple, dict] = {}
    for i, r in enumerate(reads):
        key = (r["tool"], r["label"])
        last_pos[key] = i
        items[key] = r
    reads = [items[k] for k in sorted(last_pos, key=lambda k: last_pos[k], reverse=True)]
    reads_total_bytes = sum(r["size_bytes"] for r in reads)

    messages: list[dict] = []
    conv_total_bytes = 0
    if jsonl_path and jsonl_path.is_file():
        import json as _json2
        try:
            with jsonl_path.open(encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = _json2.loads(line)
                    except Exception:
                        continue
                    role = d.get("type")
                    if role not in ("user", "assistant"):
                        continue
                    content = d.get("message", {}).get("content", []) or []
                    text_parts: list[str] = []
                    if isinstance(content, str):
                        text_parts = [content]
                    else:
                        for c in content:
                            if not isinstance(c, dict):
                                continue
                            if c.get("type") in ("tool_use", "tool_result"):
                                continue
                            if c.get("type") == "text":
                                text_parts.append(c.get("text", ""))
                    text = "\n".join(text_parts).strip()
                    if not text:
                        continue
                    if text.startswith("<command-") or text.startswith("<local-command") or text.startswith("<system-reminder"):
                        continue
                    size_bytes = len(text.encode("utf-8"))
                    conv_total_bytes += size_bytes
                    is_compaction = role == "user" and text.startswith("This session is being continued from a previous conversation")
                    messages.append({
                        "role": role,
                        "is_compaction": is_compaction,
                        "snippet": text[:120],
                        "full_text": text[:8000],
                        "total_chars": len(text),
                        "size_bytes": size_bytes,
                        "tokens_est": size_bytes // 4,
                    })
        except OSError:
            pass

    return {
        "rules": rules,
        "rules_total_bytes": rules_total_bytes,
        "rules_total_tokens_est": rules_total_bytes // 4,
        "reads": reads[:60],
        "reads_total_bytes": reads_total_bytes,
        "reads_total_tokens_est": reads_total_bytes // 4,
        "messages": list(reversed(messages[-50:])),
        "conv_total_bytes": conv_total_bytes,
        "conv_total_tokens_est": conv_total_bytes // 4,
        "session_id": str(jsonl_path.stem) if jsonl_path else None,
    }

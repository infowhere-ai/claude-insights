"""Context inspector endpoint."""

import json
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from claude_monitor import config, state
from claude_monitor.jsonl import parser

router = APIRouter(tags=["context"])

_SUPPORTED_TOOLS = ("Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch")
_CONTENT_TRUNCATE = 8000
_SNIPPET_LEN = 120
_MAX_MESSAGES = 50
_MAX_READS = 60


# ── private helpers ────────────────────────────────────────────────────────────


def _make_rule_dict(label: str, real_path: Path, category: str) -> dict | None:
    """Build a single rule dict from a resolved file path. Returns None on OSError."""
    try:
        size = real_path.stat().st_size
        return {
            "label": label,
            "real_path": str(real_path),
            "size_bytes": size,
            "tokens_est": size // 4,
            "category": category,
        }
    except OSError:
        return None


def _resolve_entry_real_path(entry: Path) -> Path | None:
    """Resolve a directory entry to its real path. Returns None on OSError or broken symlink."""
    try:
        real = entry.resolve()
    except OSError:
        return None
    # A broken symlink resolves but is not a file or directory — treat as missing
    if not real.exists():
        return None
    return real


def _collect_rules_from_subdir(real: Path, projects_root: Path, category: str) -> list[dict]:
    """Collect rule dicts for all .md files under a resolved directory."""
    collected: list[dict] = []
    for md_file in sorted(real.rglob("*.md")):
        if not md_file.is_file():
            continue
        label = md_file.name
        try:
            label = str(md_file.relative_to(projects_root))
        except ValueError:
            pass
        item = _make_rule_dict(label, md_file, category)
        if item:
            collected.append(item)
    return collected


def _add_rules_from_dir(rules_dir: Path, projects_root: Path, category: str) -> list[dict]:
    """Walk a rules directory and collect rule dicts for files and subdirectories."""
    collected: list[dict] = []
    if not rules_dir.is_dir():
        return collected
    for entry in sorted(rules_dir.iterdir()):
        real = _resolve_entry_real_path(entry)
        if real is None:
            continue
        try:
            if real.is_file():
                label = entry.name
                try:
                    label = str(real.relative_to(projects_root))
                except ValueError:
                    pass
                item = _make_rule_dict(label, real, category)
                if item:
                    collected.append(item)
            elif real.is_dir():
                collected.extend(_collect_rules_from_subdir(real, projects_root, category))
        except OSError:
            pass
    return collected


def _collect_global_rules(rules_dir: Path) -> list[dict]:
    """Collect global-rule dicts from ~/.claude/rules (files and subdirectories)."""
    collected: list[dict] = []
    if not rules_dir.is_dir():
        return collected
    for entry in sorted(rules_dir.iterdir()):
        real = _resolve_entry_real_path(entry)
        if real is None:
            continue
        try:
            if real.is_file():
                item = _make_rule_dict(f"~/.claude/rules/{entry.name}", real, "global-rule")
                if item:
                    collected.append(item)
            elif real.is_dir():
                for md_file in sorted(real.rglob("*.md")):
                    if not md_file.is_file():
                        continue
                    item = _make_rule_dict(
                        f"~/.claude/rules/{entry.name}/{md_file.name}",
                        md_file,
                        "global-rule",
                    )
                    if item:
                        collected.append(item)
        except OSError:
            pass
    return collected


def _collect_rules(project_path: Path, projects_root: Path) -> list[dict]:
    """Collect CLAUDE.md files and rules for a project.

    Returns list of rule dicts sorted by size descending.
    """
    rules: list[dict] = []

    for candidate in [project_path / "CLAUDE.md", project_path / ".claude" / "CLAUDE.md"]:
        if candidate.is_file():
            item = _make_rule_dict(candidate.name, candidate, "claude-md")
            if item:
                rules.append(item)

    if config.CLAUDE_GLOBAL_MD.is_file():
        item = _make_rule_dict("~/.claude/CLAUDE.md", config.CLAUDE_GLOBAL_MD, "global")
        if item:
            rules.append(item)

    rules.extend(_add_rules_from_dir(project_path / ".claude" / "rules", projects_root, "rule"))
    rules.extend(_collect_global_rules(config.CLAUDE_RULES_DIR))

    rules.sort(key=lambda r: r["size_bytes"], reverse=True)
    return rules


def _extract_label(inp: dict) -> str:
    """Extract a human-readable label from a tool input dict."""
    return (
        inp.get("file_path")
        or inp.get("path")
        or inp.get("command", "")[:_MAX_READS]
        or inp.get("url")
        or inp.get("query")
        or inp.get("pattern")
        or ""
    )


def _extract_result_text(content) -> str:
    """Convert a tool_result content value to a plain string."""
    if isinstance(content, list):
        return "\n".join(x.get("text", "") for x in content if isinstance(x, dict))
    return str(content)


def _collect_reads(jsonl_path: Path) -> list[dict]:
    """Parse JSONL for tool Read/Write/Edit/Bash/Glob/Grep/WebFetch results.

    Returns deduplicated list ordered by last occurrence (most recent first).
    """
    raw: list[dict] = []
    tool_uses: dict[str, dict] = {}

    try:
        with jsonl_path.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") == "assistant":
                    for c in d.get("message", {}).get("content", []) or []:
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            tool_uses[c["id"]] = {
                                "name": c.get("name", ""),
                                "input": c.get("input", {}),
                            }
                elif d.get("type") == "user":
                    raw.extend(_parse_tool_results(d, tool_uses))
    except OSError:
        return []

    return _deduplicate_reads(raw)


def _parse_tool_results(d: dict, tool_uses: dict[str, dict]) -> list[dict]:
    """Extract tool result entries from a single user JSONL line."""
    results = []
    for c in d.get("message", {}).get("content", []) or []:
        if not isinstance(c, dict) or c.get("type") != "tool_result":
            continue
        tid = c.get("tool_use_id", "")
        tu = tool_uses.get(tid, {})
        tool_name = tu.get("name", "")
        if tool_name not in _SUPPORTED_TOOLS:
            continue
        result_text = _extract_result_text(c.get("content", ""))
        size_bytes = len(result_text.encode("utf-8"))
        label = _extract_label(tu.get("input", {}))
        results.append(
            {
                "tool": tool_name,
                "label": label,
                "size_bytes": size_bytes,
                "tokens_est": size_bytes // 4,
                "is_error": c.get("is_error", False),
                "content": result_text[:_CONTENT_TRUNCATE],
                "total_chars": len(result_text),
            }
        )
    return results


def _deduplicate_reads(raw: list[dict]) -> list[dict]:
    """Keep only the last occurrence of each (tool, label) pair, most-recent first."""
    last_pos: dict[tuple, int] = {}
    items: dict[tuple, dict] = {}
    for i, r in enumerate(raw):
        key = (r["tool"], r["label"])
        last_pos[key] = i
        items[key] = r
    return [items[k] for k in sorted(last_pos, key=lambda k: last_pos[k], reverse=True)]


def _collect_messages(jsonl_path: Path) -> list[dict]:
    """Parse JSONL for user/assistant text messages (non-tool).

    Returns messages in reverse chronological order (last 50).
    """
    messages: list[dict] = []

    try:
        with jsonl_path.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                msg = _parse_message(d)
                if msg:
                    messages.append(msg)
    except OSError:
        return []

    return list(reversed(messages[-_MAX_MESSAGES:]))


def _extract_text_parts(content) -> list[str]:
    """Extract plain text parts from a message content field."""
    if isinstance(content, str):
        return [content]
    parts = []
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") in ("tool_use", "tool_result"):
            continue
        if c.get("type") == "text":
            parts.append(c.get("text", ""))
    return parts


def _parse_message(d: dict) -> dict | None:
    """Convert a single JSONL dict to a message dict, or None if it should be skipped."""
    role = d.get("type")
    if role not in ("user", "assistant"):
        return None
    content = d.get("message", {}).get("content", []) or []
    text = "\n".join(_extract_text_parts(content)).strip()
    if not text:
        return None
    if (
        text.startswith("<command-")
        or text.startswith("<local-command")
        or text.startswith("<system-reminder")
    ):
        return None
    size_bytes = len(text.encode("utf-8"))
    is_compaction = role == "user" and text.startswith(
        "This session is being continued from a previous conversation"
    )
    return {
        "role": role,
        "is_compaction": is_compaction,
        "snippet": text[:_SNIPPET_LEN],
        "full_text": text[:_CONTENT_TRUNCATE],
        "total_chars": len(text),
        "size_bytes": size_bytes,
        "tokens_est": size_bytes // 4,
    }


# ── endpoint ───────────────────────────────────────────────────────────────────


@router.get("/api/context-inspect")
async def get_context_inspect(project: str = Query(...), session_id: str = Query(default="")):
    if project not in state._status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    project_path = state._status_paths[project].parents[1]

    rules = _collect_rules(project_path, config.PROJECTS_ROOT)
    rules_total_bytes = sum(r["size_bytes"] for r in rules)

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

    reads: list[dict] = []
    reads_total_bytes = 0
    messages: list[dict] = []
    conv_total_bytes = 0

    if jsonl_path and jsonl_path.is_file():
        reads = _collect_reads(jsonl_path)
        reads_total_bytes = sum(r["size_bytes"] for r in reads)
        messages = _collect_messages(jsonl_path)
        conv_total_bytes = sum(m["size_bytes"] for m in messages)

    return {
        "rules": rules,
        "rules_total_bytes": rules_total_bytes,
        "rules_total_tokens_est": rules_total_bytes // 4,
        "reads": reads[:_MAX_READS],
        "reads_total_bytes": reads_total_bytes,
        "reads_total_tokens_est": reads_total_bytes // 4,
        "messages": messages,
        "conv_total_bytes": conv_total_bytes,
        "conv_total_tokens_est": conv_total_bytes // 4,
        "session_id": str(jsonl_path.stem) if jsonl_path else None,
    }

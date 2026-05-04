"""JSONL session file parsing — tail reads, thinking detection, session detail."""

import datetime
import difflib
import hashlib
import json
from pathlib import Path

from claude_monitor import config


def get_jsonl_dir(project_path: Path) -> Path:
    encoded = str(project_path).replace("/", "-")
    return config.CLAUDE_PROJECTS_DIR / encoded


def get_latest_jsonl(project_path: Path) -> tuple[Path | None, float]:
    jsonl_dir = get_jsonl_dir(project_path)
    if not jsonl_dir.is_dir():
        return None, 0.0
    try:
        files = list(jsonl_dir.glob("*.jsonl"))
        if not files:
            return None, 0.0
        latest = max(files, key=lambda p: p.stat().st_mtime)
        return latest, latest.stat().st_mtime
    except OSError:
        return None, 0.0


def _read_tail_bytes(jsonl_path: Path, size: int) -> str:
    """Read last N bytes of a file, returns decoded string. Empty string on error."""
    try:
        with open(jsonl_path, "rb") as fh:
            fh.seek(0, 2)
            file_size = fh.tell()
            fh.seek(max(0, file_size - size))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _extract_tool_from_content(content: list) -> str | None:
    """Find last tool_use block in assistant content list. Returns tool name or None."""
    for c in reversed(content):
        if isinstance(c, dict) and c.get("type") == "tool_use":
            return c.get("name") or "Tool"
    return None


def _parse_jsonl_tail_line(
    d: dict, current_cwd: str | None, current_tool: str | None
) -> tuple[str | None, str | None]:
    """Extract tool and cwd from a single parsed JSONL line without overwriting existing values."""
    cwd = current_cwd
    tool = current_tool
    if cwd is None and d.get("cwd"):
        cwd = d["cwd"]
    if tool is None and d.get("type") == "assistant":
        content = d.get("message", {}).get("content", [])
        if isinstance(content, list):
            tool = _extract_tool_from_content(content)
    return tool, cwd


def parse_jsonl_tail(jsonl_path: Path) -> dict:
    """Read last 8 KB of a JSONL session file. Returns {tool, cwd}."""
    raw = _read_tail_bytes(jsonl_path, 8192)
    if not raw:
        return {}
    tool: str | None = None
    cwd: str | None = None
    for line in reversed(raw.strip().split("\n")):
        try:
            d = json.loads(line)
        except Exception:
            continue
        tool, cwd = _parse_jsonl_tail_line(d, cwd, tool)
        if cwd and tool:
            break
    return {"tool": tool, "cwd": cwd}


def _extract_thinking_block(entry: dict) -> dict | None:
    """From one assistant entry, return thinking block dict or None."""
    if entry.get("type") != "assistant":
        return None
    ts = entry.get("timestamp", "")
    for c in entry.get("message", {}).get("content", []) or []:
        if isinstance(c, dict) and c.get("type") == "thinking":
            text = c.get("thinking", "").strip()
            if text:
                return {
                    "block_id": hashlib.md5(ts.encode()).hexdigest()[:12],
                    "text": text,
                    "word_count": len(text.split()),
                    "timestamp": ts,
                }
    return None


def detect_latest_thinking(jsonl_path: Path) -> dict | None:
    """Reads last 32 KB of JSONL, returns most recent non-empty thinking block."""
    raw = _read_tail_bytes(jsonl_path, 32768)
    if not raw:
        return None
    last = None
    for line in raw.strip().split("\n"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        block = _extract_thinking_block(d)
        if block is not None:
            last = block
    return last


def tool_input_summary(name: str, inp: dict) -> str:
    if name in ("Read", "Write", "Edit"):
        return inp.get("file_path", inp.get("path", ""))
    if name == "Bash":
        return inp.get("command", "")[:80]
    if name in ("Glob", "Grep"):
        return inp.get("pattern", inp.get("path", ""))
    if name in ("WebFetch", "WebSearch"):
        return inp.get("url", inp.get("query", ""))
    for v in inp.values():
        if isinstance(v, str):
            return v[:80]
    return ""


def tool_detail(name: str, inp: dict) -> dict:
    if name == "Bash":
        return {
            "type": "bash",
            "command": inp.get("command", ""),
            "description": inp.get("description", ""),
        }
    if name == "Edit":
        file_path = inp.get("file_path", inp.get("path", ""))
        old = inp.get("old_string", "")
        new = inp.get("new_string", "")
        diff_lines = list(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
                lineterm="",
            )
        )
        return {"type": "edit", "file_path": file_path, "diff": "".join(diff_lines)}
    if name == "Write":
        content = inp.get("content", "")
        return {
            "type": "write",
            "file_path": inp.get("file_path", inp.get("path", "")),
            "content": content[:3000],
            "total_chars": len(content),
        }
    if name == "Read":
        return {
            "type": "read",
            "file_path": inp.get("file_path", inp.get("path", "")),
            "limit": inp.get("limit"),
            "offset": inp.get("offset"),
        }
    if name in ("Grep", "Glob"):
        return {
            "type": "search",
            "tool": name,
            "pattern": inp.get("pattern", ""),
            "path": inp.get("path", ""),
            "include": inp.get("include", ""),
        }
    if name in ("WebFetch", "WebSearch"):
        return {"type": "web", "url": inp.get("url", ""), "query": inp.get("query", "")}
    if name == "Agent":
        return {
            "type": "agent",
            "description": inp.get("description", ""),
            "prompt": inp.get("prompt", inp.get("instructions", ""))[:500],
        }
    return {
        "type": "generic",
        "fields": {k: str(v)[:500] for k, v in inp.items() if isinstance(v, str)},
    }


def _read_jsonl_entries(jsonl_path: Path) -> list[dict]:
    """Read all JSONL entries from a file, skipping blank and invalid lines."""
    entries: list[dict] = []
    try:
        with jsonl_path.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        return []
    return entries


def _collect_tool_results(entries: list[dict]) -> dict[str, dict]:
    """Build {tool_use_id: {timestamp, is_error}} from user entries."""
    tool_results: dict[str, dict] = {}
    for entry in entries:
        if entry.get("type") != "user":
            continue
        for c in entry.get("message", {}).get("content", []) or []:
            if isinstance(c, dict) and c.get("type") == "tool_result":
                tool_results[c.get("tool_use_id", "")] = {
                    "timestamp": entry.get("timestamp"),
                    "is_error": c.get("is_error", False),
                }
    return tool_results


def _calculate_duration_ms(ts: str, rts: str) -> int | None:
    """Calculate milliseconds between two ISO timestamps. Returns None on failure."""
    if not ts or not rts:
        return None
    try:
        t1 = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        t2 = datetime.datetime.fromisoformat(rts.replace("Z", "+00:00"))
        return int((t2 - t1).total_seconds() * 1000)
    except Exception:
        return None


def _process_assistant_entry(
    entry: dict,
    tool_results: dict[str, dict],
    thinking: list,
    tools: list,
    stats: dict,
) -> None:
    """Process one assistant entry: update stats, append thinking/tool events."""
    msg = entry.get("message", {})
    content = msg.get("content", [])
    if not isinstance(content, list):
        return
    ts = entry.get("timestamp", "")
    u = msg.get("usage", {})
    stats["input_tokens"] += u.get("input_tokens", 0)
    stats["output_tokens"] += u.get("output_tokens", 0)
    stats["cache_read_tokens"] += u.get("cache_read_input_tokens", 0)
    m = msg.get("model", "")
    if m:
        stats["model"] = m
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "thinking":
            text = c.get("thinking", "").strip()
            if text:
                thinking.append({"text": text, "timestamp": ts, "word_count": len(text.split())})
        if c.get("type") == "tool_use":
            tid = c.get("id", "")
            tname = c.get("name", "")
            tinput = c.get("input", {})
            result = tool_results.get(tid, {})
            duration_ms = _calculate_duration_ms(ts, result.get("timestamp") or "")
            tools.append(
                {
                    "tool": tname,
                    "input": tool_input_summary(tname, tinput),
                    "detail": tool_detail(tname, tinput),
                    "duration_ms": duration_ms,
                    "success": not result.get("is_error", False),
                    "timestamp": ts,
                }
            )


def parse_session_detail(jsonl_path: Path) -> dict:
    thinking: list = []
    tools: list = []
    stats = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "model": ""}
    entries = _read_jsonl_entries(jsonl_path)
    if not entries and not jsonl_path.exists():
        return {"thinking": [], "tools": [], "stats": stats}
    tool_results = _collect_tool_results(entries)
    for entry in entries:
        if entry.get("type") == "assistant":
            _process_assistant_entry(entry, tool_results, thinking, tools, stats)
    return {"thinking": thinking, "tools": tools[-50:], "stats": stats}

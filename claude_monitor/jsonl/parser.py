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


def parse_jsonl_tail(jsonl_path: Path) -> dict:
    """Read last 8 KB of a JSONL session file. Returns {tool, cwd}."""
    try:
        with open(jsonl_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            raw = fh.read().decode("utf-8", errors="replace")
        tool: str | None = None
        cwd: str | None = None
        for line in reversed(raw.strip().split("\n")):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not cwd and d.get("cwd"):
                cwd = d["cwd"]
            if tool is None and d.get("type") == "assistant":
                content = d.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for c in reversed(content):
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            tool = c.get("name", "Tool")
                            break
            if cwd and tool:
                break
        return {"tool": tool, "cwd": cwd}
    except Exception:
        return {}


def detect_latest_thinking(jsonl_path: Path) -> dict | None:
    """Reads last 32 KB of JSONL, returns most recent non-empty thinking block."""
    try:
        with open(jsonl_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 32768))
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    last = None
    for line in raw.strip().split("\n"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "assistant":
            continue
        ts = d.get("timestamp", "")
        for c in (d.get("message", {}).get("content", []) or []):
            if isinstance(c, dict) and c.get("type") == "thinking":
                text = c.get("thinking", "").strip()
                if text:
                    block_id = hashlib.md5(ts.encode()).hexdigest()[:12]
                    last = {"block_id": block_id, "text": text,
                            "word_count": len(text.split()),
                            "timestamp": ts}
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
        return {"type": "bash", "command": inp.get("command", ""),
                "description": inp.get("description", "")}
    if name == "Edit":
        file_path = inp.get("file_path", inp.get("path", ""))
        old = inp.get("old_string", "")
        new = inp.get("new_string", "")
        diff_lines = list(difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm="",
        ))
        return {"type": "edit", "file_path": file_path, "diff": "".join(diff_lines)}
    if name == "Write":
        content = inp.get("content", "")
        return {"type": "write", "file_path": inp.get("file_path", inp.get("path", "")),
                "content": content[:3000], "total_chars": len(content)}
    if name == "Read":
        return {"type": "read", "file_path": inp.get("file_path", inp.get("path", "")),
                "limit": inp.get("limit"), "offset": inp.get("offset")}
    if name in ("Grep", "Glob"):
        return {"type": "search", "tool": name, "pattern": inp.get("pattern", ""),
                "path": inp.get("path", ""), "include": inp.get("include", "")}
    if name in ("WebFetch", "WebSearch"):
        return {"type": "web", "url": inp.get("url", ""), "query": inp.get("query", "")}
    if name == "Agent":
        return {"type": "agent", "description": inp.get("description", ""),
                "prompt": inp.get("prompt", inp.get("instructions", ""))[:500]}
    return {"type": "generic",
            "fields": {k: str(v)[:500] for k, v in inp.items() if isinstance(v, str)}}


def parse_session_detail(jsonl_path: Path) -> dict:
    thinking = []
    tools = []
    stats = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "model": ""}
    entries = []
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
        return {"thinking": [], "tools": [], "stats": stats}

    tool_results: dict[str, dict] = {}
    for entry in entries:
        if entry.get("type") != "user":
            continue
        for c in (entry.get("message", {}).get("content", []) or []):
            if isinstance(c, dict) and c.get("type") == "tool_result":
                tool_results[c.get("tool_use_id", "")] = {
                    "timestamp": entry.get("timestamp"),
                    "is_error": c.get("is_error", False),
                }

    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", [])
        ts = entry.get("timestamp", "")
        if not isinstance(content, list):
            continue
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
                    thinking.append({"text": text, "timestamp": ts,
                                     "word_count": len(text.split())})
            if c.get("type") == "tool_use":
                tid = c.get("id", "")
                tname = c.get("name", "")
                tinput = c.get("input", {})
                result = tool_results.get(tid, {})
                duration_ms = None
                rts = result.get("timestamp")
                if ts and rts:
                    try:
                        t1 = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        t2 = datetime.datetime.fromisoformat(rts.replace("Z", "+00:00"))
                        duration_ms = int((t2 - t1).total_seconds() * 1000)
                    except Exception:
                        pass
                tools.append({
                    "tool": tname,
                    "input": tool_input_summary(tname, tinput),
                    "detail": tool_detail(tname, tinput),
                    "duration_ms": duration_ms,
                    "success": not result.get("is_error", False),
                    "timestamp": ts,
                })
    return {"thinking": thinking, "tools": tools[-50:], "stats": stats}

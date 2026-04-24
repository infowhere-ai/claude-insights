# Claude Insights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/insights` page to claude-monitor showing Claude's thinking blocks, tool activity, session history, and weekly stats — with IntelliJ-style collapsible panels.

**Architecture:** New standalone `static/insights.html` served by `GET /insights`. Four new backend endpoints read JSONL files from `~/.claude/projects/<encoded>/`. The existing SSE `/events` stream gains `thinking` events. Frontend is pure HTML/JS, no framework — same as `index.html`.

**Tech Stack:** Python 3.12 + FastAPI, HTML/CSS/JS (no build step), SSE.

**Reference mockups:** `.superpowers/brainstorm/18114-1777023637/panels.html` (IntelliJ panel system), `before-after.html` (full layout comparison).

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `app.py` | Modify | 4 endpoints + `/insights` route + SSE thinking |
| `static/insights.html` | Create | Full insights page |
| `static/index.html` | Modify | Add `✦ Insights` link in navbar |

---

## JSONL Structure Reference

Sessions: `~/.claude/projects/<encoded-path>/<uuid>.jsonl` (flat files — root-level only; ignore `<uuid-dir>/subagents/`).

Assistant message with thinking + tool_use:
```
{"type":"assistant","timestamp":"2026-04-24T10:00:00.000Z",
 "message":{"content":[
   {"type":"thinking","thinking":"I need to..."},
   {"type":"tool_use","id":"toolu_abc","name":"Read","input":{"file_path":"app.py"}}
 ],"usage":{"input_tokens":14,"output_tokens":876,"cache_read_input_tokens":384000}}}
```

User message with tool_result:
```
{"type":"user","timestamp":"2026-04-24T10:00:01.200Z",
 "message":{"content":[
   {"type":"tool_result","tool_use_id":"toolu_abc","content":"...","is_error":false}
 ]}}
```

Tool input summary (string, max 80 chars):
- Read/Write/Edit/Glob/Grep: `input["file_path"]` or `input["pattern"]`
- Bash: `input["command"][:80]`
- WebFetch/WebSearch: `input["url"]` or `input["query"]`
- Others: first string value in input dict

Duration = `tool_result.timestamp - tool_use.timestamp` (pair by `tool_use_id`).
Success = `not tool_result.get("is_error", False)`.

---

## Task 1: Backend — `/api/sessions`

**Files:** Modify `app.py` (after `get_weekly_stats`, line ~937)

> **Filesystem note (verified):** Sessions are flat `.jsonl` files at root level of `~/.claude/projects/<encoded>/` (e.g. `272efe8e-8d74-4775.jsonl`). They are NOT directories. Subdirectories like `<uuid>/subagents/` exist but should be ignored — glob `*.jsonl` at root only.

- [ ] **Step 0: Verify encoding formula against real filesystem**

The existing `app.py` already uses this encoding to find JSONL files — search for where `CLAUDE_PROJECTS_DIR` is used in the file to confirm the exact formula before implementing. Then verify it produces a matching directory:
```bash
python3 -c "
import pathlib
p = pathlib.Path('/Users/leandrosiciliano/desenvolvimento/github-infowhere/claude-monitor')
encoded = str(p).replace('/', '-')
print(encoded)
import os; print(os.path.isdir(pathlib.Path.home() / '.claude/projects' / encoded))
"
```
Expected: `True`. If `False`, check actual directory names with `ls ~/.claude/projects/ | head -5` and adjust the formula.

- [ ] **Step 1: Add `_list_sessions` helper after `_get_project_stats`**

```python
def _list_sessions(project_name: str) -> list[dict]:
    """Lists root-level JSONL sessions for a project, newest-first."""
    if project_name not in _status_paths:
        return []
    project_path = _status_paths[project_name].parents[1]
    encoded = str(project_path).replace("/", "-")
    jsonl_dir = CLAUDE_PROJECTS_DIR / encoded
    if not jsonl_dir.is_dir():
        return []
    now = time.time()
    sessions = []
    try:
        for f in jsonl_dir.glob("*.jsonl"):
            try:
                mtime = f.stat().st_mtime
                is_active = (now - mtime) <= JSONL_ACTIVE_SECONDS
                started_at = ended_at = None
                with f.open(encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                            ts = d.get("timestamp")
                            if ts and started_at is None:
                                started_at = ts
                            if ts:
                                ended_at = ts
                        except Exception:
                            continue
                sessions.append({
                    "session_id": f.stem,
                    "started_at": started_at,
                    "ended_at": None if is_active else ended_at,
                    "is_active": is_active,
                    "_mtime": mtime,
                })
            except OSError:
                continue
    except OSError:
        return []
    sessions.sort(key=lambda s: s["_mtime"], reverse=True)
    for s in sessions:
        del s["_mtime"]
    return sessions
```

- [ ] **Step 2: Add endpoint**

```python
@app.get("/api/sessions")
async def get_sessions(project: str = Query(...)):
    sessions = _list_sessions(project)
    if not sessions and project not in _status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    return sessions
```

- [ ] **Step 3: Test**
```bash
curl "http://localhost:9001/api/sessions?project=claude-monitor" | python3 -m json.tool
```
Expected: array with session_id, started_at, ended_at, is_active.

- [ ] **Step 4: Commit**
```bash
git add app.py && git commit -m "feat(app): add /api/sessions endpoint"
```

---

## Task 2: Backend — `/api/session-detail`

**Files:** Modify `app.py`

> **Filesystem note (verified — same as Task 1):** `session_id` is the stem of a flat `.jsonl` file (e.g. `272efe8e-8d74-4775-932a-d83c78db6d09`). The endpoint path is `CLAUDE_PROJECTS_DIR / encoded / f"{session_id}.jsonl"`. Do NOT look for a directory named `session_id` — subdirectories exist but are subagent sessions and should be ignored here.

- [ ] **Step 1: Add `_tool_input_summary` and `_parse_session_detail` helpers**

```python
def _tool_input_summary(name: str, inp: dict) -> str:
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


def _parse_session_detail(jsonl_path: Path) -> dict:
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

    # Index tool_results by id for duration + success
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
        stats["input_tokens"]      += u.get("input_tokens", 0)
        stats["output_tokens"]     += u.get("output_tokens", 0)
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
                    "tool": c.get("name", ""),
                    "input": _tool_input_summary(c.get("name", ""), c.get("input", {})),
                    "duration_ms": duration_ms,
                    "success": not result.get("is_error", False),
                    "timestamp": ts,
                })
    return {"thinking": thinking[-5:], "tools": tools[-20:], "stats": stats}
```

- [ ] **Step 2: Add endpoint**

```python
@app.get("/api/session-detail")
async def get_session_detail(project: str = Query(...), session_id: str = Query(...)):
    if project not in _status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    project_path = _status_paths[project].parents[1]
    encoded = str(project_path).replace("/", "-")
    jsonl_path = CLAUDE_PROJECTS_DIR / encoded / f"{session_id}.jsonl"
    if not jsonl_path.is_file():
        return JSONResponse({"error": "session not found"}, status_code=404)
    return _parse_session_detail(jsonl_path)
```

- [ ] **Step 3: Test**
```bash
SESSION=$(curl -s "http://localhost:9001/api/sessions?project=claude-monitor" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[0]['session_id'])")
curl "http://localhost:9001/api/session-detail?project=claude-monitor&session_id=$SESSION" \
  | python3 -m json.tool | head -40
```
Expected: thinking array with word_count, tools array with duration_ms, stats.

- [ ] **Step 4: Commit**
```bash
git add app.py && git commit -m "feat(app): add /api/session-detail endpoint"
```

---

## Task 3: Backend — `/api/insights-stats`

**Files:** Modify `app.py`

- [ ] **Step 1: Add endpoint**

```python
@app.get("/api/insights-stats")
async def get_insights_stats(project: str = Query(...)):
    """Aggregated metrics for the last 7 days."""
    if project not in _status_paths:
        return JSONResponse({"error": "project not found"}, status_code=404)
    project_path = _status_paths[project].parents[1]
    encoded = str(project_path).replace("/", "-")
    jsonl_dir = CLAUDE_PROJECTS_DIR / encoded
    if not jsonl_dir.is_dir():
        return {"sessions_count": 0, "total_tokens": 0,
                "cache_hit_pct": 0, "top_tool": None, "top_tool_count": 0}
    cutoff = time.time() - 7 * 24 * 3600
    sessions_count = total_input = total_output = total_cache = 0
    tool_counts: dict[str, int] = {}
    try:
        for f in jsonl_dir.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    continue
                sessions_count += 1
                with f.open(encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        if d.get("type") == "assistant":
                            u = d.get("message", {}).get("usage", {})
                            total_input  += u.get("input_tokens", 0)
                            total_output += u.get("output_tokens", 0)
                            total_cache  += u.get("cache_read_input_tokens", 0)
                            for c in d.get("message", {}).get("content", []):
                                if isinstance(c, dict) and c.get("type") == "tool_use":
                                    n = c.get("name", "")
                                    if n:
                                        tool_counts[n] = tool_counts.get(n, 0) + 1
            except OSError:
                continue
    except OSError:
        pass
    total_tokens = total_input + total_output
    total_real   = total_input + total_cache
    cache_hit_pct = round(total_cache / total_real * 100) if total_real > 0 else 0
    top_tool = max(tool_counts, key=tool_counts.get) if tool_counts else None
    return {
        "sessions_count": sessions_count,
        "total_tokens": total_tokens,
        "cache_hit_pct": cache_hit_pct,
        "top_tool": top_tool,
        "top_tool_count": tool_counts.get(top_tool, 0) if top_tool else 0,
    }
```

- [ ] **Step 2: Test**
```bash
curl "http://localhost:9001/api/insights-stats?project=claude-monitor" | python3 -m json.tool
```
Expected: sessions_count > 0, top_tool = "Read" or similar.

- [ ] **Step 3: Commit**
```bash
git add app.py && git commit -m "feat(app): add /api/insights-stats endpoint"
```

---

## Task 4: SSE thinking events + `/insights` route

**Files:** Modify `app.py`

- [ ] **Step 1: Add `_thinking_cache` dict after `_agents_dir_mtimes` (line ~56)**

```python
_thinking_cache: dict[str, dict] = {}  # project_name -> {block_id, text, mtime}
```

- [ ] **Step 2a: Ensure `hashlib` is imported**
```bash
grep "import hashlib" app.py
```
If not present, add `import hashlib` to the stdlib imports block at the top of `app.py`.

- [ ] **Step 2: Add `_detect_latest_thinking` helper (after `_parse_jsonl_tail`)**

```python
def _detect_latest_thinking(jsonl_path: Path) -> dict | None:
    """Reads last 32 KB of JSONL, returns most recent non-empty thinking block.

    block_id = MD5 hash of thinking text (first 12 hex chars) — trivially stable
    across repeated calls regardless of file growth or tail-window shifts.
    Note: add 'import hashlib' to the top-level imports if not already present.
    """
    try:
        with open(jsonl_path, "rb") as fh:
            fh.seek(0, 2); size = fh.tell()
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
                    block_id = hashlib.md5(ts.encode()).hexdigest()[:12]  # stable: same entry ts = same block
                    last = {"block_id": block_id, "text": text,
                            "word_count": len(text.split()),
                            "timestamp": ts}
    return last
```

- [ ] **Step 3: In `jsonl_watcher_loop`, emit thinking events**

Inside `if cached.get("mtime") != latest_mtime:` block, after updating `_jsonl_cache`, add:

```python
                    thinking = _detect_latest_thinking(latest_jsonl)
                    if thinking:
                        prev = _thinking_cache.get(name, {})
                        if (prev.get("block_id") != thinking["block_id"] or
                                prev.get("text") != thinking["text"]):
                            _thinking_cache[name] = {
                                "block_id": thinking["block_id"],
                                "text": thinking["text"],
                                "mtime": latest_mtime,
                            }
                            _broadcast({
                                "type": "thinking",
                                "project": name,
                                "block_id": thinking["block_id"],
                                "text": thinking["text"],
                                "word_count": thinking["word_count"],
                                "timestamp": thinking["timestamp"],
                            })
```

- [ ] **Step 4: Add `/insights` route (after `/health`)**

First check if `FileResponse` is already imported at the top of `app.py`:
```bash
grep "FileResponse" app.py | head -3
```
If not present, add `FileResponse` to the existing `from fastapi.responses import ...` line (or add `from fastapi.responses import FileResponse` at the top). Then add the route:

```python
@app.get("/insights")
async def insights_page():
    return FileResponse("static/insights.html")
```

- [ ] **Step 5: Test SSE**
```bash
curl -N http://localhost:9001/events | grep '"type":"thinking"'
```
With Claude running in another session, thinking events should appear.

- [ ] **Step 6: Commit**
```bash
git add app.py && git commit -m "feat(app): SSE thinking events and /insights route"
```

---

## Task 5: Add "✦ Insights" link to navbar

**Files:** Modify `static/index.html`

- [ ] **Step 1: Find navbar**
```bash
grep -n "Claude Monitor\|toolbar\|nav-" static/index.html | head -15
```

- [ ] **Step 2: Insert link inside the toolbar/header**
```html
<a href="/insights" style="color:#3b82f6;text-decoration:none;font-size:10px;padding:2px 8px;border:1px solid #1e3a5f;border-radius:4px;">✦ Insights</a>
```

- [ ] **Step 3: Test** — navigate to `http://localhost:9001`, confirm link appears.

- [ ] **Step 4: Commit**
```bash
git add static/index.html && git commit -m "feat(frontend): add Insights link to monitor navbar"
```

---

## Task 6: `static/insights.html` — full page

**Files:** Create `static/insights.html`

Reference: `.superpowers/brainstorm/18114-1777023637/panels.html` for the IntelliJ panel system.
Follow the same CSS variable palette and monospace font as `index.html`.

### Layout structure

```
body (flex column, 100vh, overflow hidden)
├── #header (flex row, justify space-between)
│   ├── "✦ Claude Insights"
│   └── "← Monitor" + <select id="project-select">
└── #body (flex row, flex 1)
    ├── .sidebar.left (30px, icons: btn-think btn-tools btn-tokens)
    ├── #main (flex 1, flex row)
    │   ├── #col-live (flex 0 0 56%, border-right, overflow-y auto)
    │   │   ├── #live-badge (● Live · project-name)
    │   │   ├── #pane-think  (.panel panel-think)
    │   │   ├── #pane-tools  (.panel panel-tools, flex:1 = grow)
    │   │   └── #pane-tokens (.panel panel-tokens)
    │   └── #col-hist (flex 1, overflow-y auto)
    │       ├── #pane-stats (.panel panel-stats)
    │       ├── #pane-sess  (.panel panel-sess, flex:1 = grow)
    │       └── #pane-git   (.panel panel-git)
    └── .sidebar.right (30px, icons: btn-stats btn-sess btn-git)
```

### Panel CSS

```css
.panel { border-radius:6px; padding:9px; flex-shrink:0; display:flex; flex-direction:column; gap:6px; }
.panel.hidden { display:none !important; }
.panel.grow   { flex:1; min-height:0; overflow-y:auto; }
```

### Sidebar buttons

Active: `background: rgba(59,130,246,0.15); color:#3b82f6`
Inactive (panel closed): `color:#1e3a5f` (barely visible)

Click toggles `.hidden` on the panel and `.active/.inactive` on the button.
After each toggle, call `redistributeGrow()` to move the `grow` class:
- Left col: tools grows first; if tools hidden, think grows
- Right col: sess grows first; if sess hidden, stats grows

### JavaScript data flow

```
connectSSE()
  └── on init: populateProjects(msg.projects) → switchProject(first active)
      └── loadSessions()     → GET /api/sessions?project=       → renderSessions() → selectActiveSession()
          └── loadSessionDetail(id) → GET /api/session-detail?project=&session_id= → populate thinking + tools + tokens
      └── loadStats()        → GET /api/insights-stats?project= → updateStats grid
      └── loadPendingFiles() → GET /api/pending?project=        → renderGit()   (existing endpoint)
  └── on thinking (project matches, isLive): setThinking(text, block_id)
  └── on update   (project matches, isLive): updateTokens(stats) + loadPendingFiles()
```

### Thinking event handling

`block_id` same as `lastBlockId` → replace current block text.
`block_id` changed → new block (update display, update `lastBlockId`).

Panel shows last 4 lines clamped; click expands to full text.

### Diff modal

Open: click file in git panel → `openDiff(index)`.
Load: GET `/api/diff?project=X&file=rel_path` → parse raw diff string.
Render: split by `\n`, classify each line:
- starts with `@@` → `.diff-hunk` class (dim color)
- starts with `+`  → `.diff-add` class (green background)
- starts with `-`  → `.diff-del` class (red background)
- otherwise        → `.diff-ctx` class (muted)

Close: ✕ button, backdrop click, or Escape.
Navigate: ← → buttons or ArrowLeft/ArrowRight keyboard events.

### Tokens display

Context window = 200,000 tokens (standard) or 1,000,000 (extended).
Use same `resolveCtxWindow` logic as `index.html` — copy that function.
Bar color: ok (green, ≤50%), warn (yellow, 51-80%), crit (red, >80%).

### Testing checklist

- [ ] Page loads without errors at `http://localhost:9001/insights`
- [ ] Project selector populated from SSE init event
- [ ] Sessions list shows real UUIDs
- [ ] Clicking a session loads its thinking + tools
- [ ] Token bar fills correctly for a real session
- [ ] Stats grid shows non-zero data
- [ ] Git files appear and clicking opens diff modal
- [ ] Diff modal: ← → navigation, Escape closes
- [ ] SSE thinking events update left column in real time
- [ ] Panel toggle: icons dim/brighten, panels hide/show, remaining panels expand

- [ ] **Commit**
```bash
git add static/insights.html && git commit -m "feat(frontend): insights page — panels, SSE, session replay, diff modal"
```

---

## Task 7: Final integration

- [ ] Verify `http://localhost:9001` (monitor) still works unchanged
- [ ] Verify `✦ Insights` link navigates correctly
- [ ] Verify `/insights` loads with real data (stats non-zero)
- [ ] Verify no-active-session placeholder (not an error)

```bash
git add . && git commit -m "feat(insights): complete Claude Insights page"
```

import asyncio
import fcntl
import json
import os
import pty
import shutil
import struct
import subprocess
import termios
from pathlib import Path

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

PROJECTS_ROOT = Path(os.getenv("PROJECTS_ROOT", str(Path.home() / "desenvolvimento" / "github-infowhere")))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "1.0"))
DISCOVERY_INTERVAL = float(os.getenv("DISCOVERY_INTERVAL", "60.0"))

app = FastAPI(title="claude-monitor", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Estado em memória
projects: dict[str, dict] = {}         # project_name -> status dict
_status_paths: dict[str, Path] = {}    # project_name -> path to status.json
_mtimes: dict[str, float] = {}         # path_str -> mtime
_sse_clients: list[asyncio.Queue] = [] # uma queue por cliente SSE

# Config: extra roots (beyond PROJECTS_ROOT)
_CONFIG_FILE = PROJECTS_ROOT / ".claude" / "monitor-roots.json"
_extra_roots: list[Path] = []


def _load_roots_config() -> None:
    global _extra_roots
    try:
        if _CONFIG_FILE.exists():
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            _extra_roots = [Path(p) for p in data.get("extra_roots", []) if Path(p).is_dir()]
    except Exception:
        _extra_roots = []


def _save_roots_config() -> None:
    try:
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(
            json.dumps({"extra_roots": [str(p) for p in _extra_roots]}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _discover() -> None:
    """Descobre projectos com .claude/status.json em PROJECTS_ROOT e roots extras."""
    found: set[str] = set()

    def _scan_root(root: Path) -> None:
        for status_path in root.glob("*/.claude/status.json"):
            name = status_path.parts[-3]
            found.add(name)
            if name not in _status_paths:
                _status_paths[name] = status_path

    _scan_root(PROJECTS_ROOT)
    for root in _extra_roots:
        _scan_root(root)

    # Remover projectos cujo ficheiro desapareceu
    gone = set(_status_paths.keys()) - found
    for name in gone:
        _status_paths.pop(name, None)
        projects.pop(name, None)
        _mtimes.pop(name, None)


def _read_status(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _broadcast(data: dict) -> None:
    for q in _sse_clients:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass


async def discovery_loop() -> None:
    while True:
        _discover()
        await asyncio.sleep(DISCOVERY_INTERVAL)


async def poll_loop() -> None:
    # Espera inicial para deixar o discovery correr primeiro
    await asyncio.sleep(2)
    while True:
        for name, path in list(_status_paths.items()):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            path_str = str(path)
            if _mtimes.get(path_str) != mtime:
                _mtimes[path_str] = mtime
                data = _read_status(path)
                if data is not None:
                    projects[name] = data
                    _broadcast({"type": "update", "project_name": name, "data": data})
        await asyncio.sleep(POLL_INTERVAL)


@app.on_event("startup")
async def startup() -> None:
    _load_roots_config()
    _discover()
    # Leitura inicial de todos os status.json
    for name, path in _status_paths.items():
        data = _read_status(path)
        if data is not None:
            projects[name] = data
            _mtimes[str(path)] = path.stat().st_mtime
    asyncio.create_task(discovery_loop())
    asyncio.create_task(poll_loop())


@app.get("/health")
async def health():
    return {"status": "ok", "projects_monitored": len(projects)}


@app.get("/api/diff")
async def get_diff(project: str = Query(...), file: str = Query(...)):
    """Retorna o git diff do ficheiro especificado no projecto."""
    project_path = PROJECTS_ROOT / project
    if not project_path.is_dir():
        return JSONResponse({"error": "project not found"}, status_code=404)

    file_path = Path(file)
    # Aceita path absoluto ou relativo ao projecto
    if not file_path.is_absolute():
        file_path = project_path / file_path

    if not file_path.is_file():
        return JSONResponse({"error": "file not found", "diff": ""})

    try:
        # 1. Tenta diff vs HEAD (ficheiro tracked com alterações não committed)
        result = subprocess.run(
            ["git", "diff", "HEAD", "--", str(file_path)],
            cwd=str(project_path),
            capture_output=True, text=True, timeout=10,
        )
        diff = result.stdout.strip()

        # 2. Se não há diff vs HEAD, tenta staged only
        if not diff:
            result2 = subprocess.run(
                ["git", "diff", "--cached", "--", str(file_path)],
                cwd=str(project_path),
                capture_output=True, text=True, timeout=10,
            )
            diff = result2.stdout.strip()

        # 3. Apenas para ficheiros UNTRACKED (não conhecidos pelo git) mostra o
        #    ficheiro completo como novo. Ficheiros tracked sem alterações
        #    retornam diff vazio — não devem cair neste fallback.
        is_untracked = False
        if not diff:
            ls_result = subprocess.run(
                ["git", "ls-files", "--error-unmatch", str(file_path)],
                cwd=str(project_path),
                capture_output=True, text=True, timeout=5,
            )
            is_untracked = ls_result.returncode != 0
            if is_untracked:
                result3 = subprocess.run(
                    ["git", "diff", "--no-index", "/dev/null", str(file_path)],
                    cwd=str(project_path),
                    capture_output=True, text=True, timeout=10,
                )
                diff = result3.stdout.strip()

        return JSONResponse({"diff": diff, "file": str(file_path), "is_new": is_untracked})
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "timeout"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/status")
async def get_status():
    return {"projects": projects, "connected_clients": len(_sse_clients)}


@app.get("/events")
async def sse_events(request: Request):
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _sse_clients.append(queue)

    async def event_generator():
        # Estado inicial ao conectar
        yield f"data: {json.dumps({'type': 'init', 'projects': projects})}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            try:
                _sse_clients.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket):
    """Spawna o claude CLI num PTY e faz bridge com o browser via WebSocket."""
    await websocket.accept()

    claude_path = shutil.which("claude")
    if not claude_path:
        await websocket.send_bytes(b"\r\n\x1b[31mErro: 'claude' nao encontrado no PATH\x1b[0m\r\n")
        await websocket.close()
        return

    master_fd, slave_fd = pty.openpty()

    def _set_winsize(fd: int, rows: int, cols: int) -> None:
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    _set_winsize(master_fd, 24, 120)

    env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"}

    proc = subprocess.Popen(
        [claude_path],
        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        env=env, close_fds=True, cwd=str(Path.home()),
    )
    os.close(slave_fd)

    loop = asyncio.get_event_loop()
    alive = True

    async def pty_to_ws() -> None:
        nonlocal alive
        try:
            while alive:
                data = await loop.run_in_executor(None, lambda: os.read(master_fd, 4096))
                if not data:
                    break
                await websocket.send_bytes(data)
        except (OSError, Exception):
            pass
        finally:
            alive = False

    async def ws_to_pty() -> None:
        nonlocal alive
        try:
            while alive:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                raw = msg.get("bytes") or (msg.get("text", "").encode() if msg.get("text") else None)
                if not raw:
                    continue
                # Mensagens de controlo chegam como JSON em texto
                try:
                    obj = json.loads(raw)
                    if obj.get("type") == "resize":
                        _set_winsize(master_fd, int(obj["rows"]), int(obj["cols"]))
                    continue
                except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
                    pass
                try:
                    os.write(master_fd, raw)
                except OSError:
                    break
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            alive = False

    t1 = asyncio.create_task(pty_to_ws())
    t2 = asyncio.create_task(ws_to_pty())
    try:
        await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
    finally:
        alive = False
        t1.cancel()
        t2.cancel()
        try:
            proc.kill()
        except Exception:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass


@app.get("/api/weekly-stats")
async def get_weekly_stats():
    """Returns weekly token totals across all monitored projects."""
    result = {}
    for name, path in _status_paths.items():
        weekly_file = path.parent / "weekly_tokens.json"
        try:
            if weekly_file.exists():
                result[name] = json.loads(weekly_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"weekly": result}


def _parse_skill_md(content: str, name: str) -> dict:
    """Parse SKILL.md: extract YAML frontmatter + first body paragraph + heading."""
    lines = content.splitlines()
    frontmatter: dict[str, str] = {}
    body_start = 0

    # Parse YAML frontmatter between --- delimiters
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

    # Extract first non-empty body paragraph (skip headings)
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

    # Fall back: use heading as title if no frontmatter name
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


@app.get("/api/skills")
async def get_skills():
    """Returns list of available skills from ~/.claude/skills/ and standarts."""
    skills = []
    search_dirs = [
        (Path.home() / ".claude" / "skills", "user"),
        (PROJECTS_ROOT / "standarts" / "common" / "skills", "common"),
        (PROJECTS_ROOT / "standarts" / "private" / "skills", "private"),
        (PROJECTS_ROOT / "standarts" / "work" / "skills", "work"),
    ]
    for base, source in search_dirs:
        if not base.is_dir():
            continue
        for skill_md in base.glob("*/SKILL.md"):
            name = skill_md.parent.name
            try:
                content = skill_md.read_text(encoding="utf-8")
                parsed = _parse_skill_md(content, name)
                skills.append({**parsed, "source": source, "path": str(skill_md)})
            except Exception:
                skills.append({
                    "name": name, "title": name, "description": "",
                    "argument_hint": "", "body_intro": "",
                    "source": source, "path": str(skill_md),
                })
    skills.sort(key=lambda s: (s["source"], s["name"]))
    return {"skills": skills}


@app.get("/api/browse")
async def browse_directory(path: str = Query(default="")):
    """Lists subdirectories at path for the directory picker UI."""
    target = Path(path).expanduser().resolve() if path else Path.home()
    if not target.is_dir():
        return JSONResponse({"error": "not a directory"}, status_code=400)
    try:
        dirs = sorted(
            [str(p) for p in target.iterdir() if p.is_dir() and not p.name.startswith(".")],
            key=lambda s: s.lower(),
        )
    except PermissionError:
        return JSONResponse({"error": "permission denied"}, status_code=403)
    parent = str(target.parent) if target.parent != target else None
    return {"current": str(target), "parent": parent, "dirs": dirs}


@app.get("/api/config")
async def get_config():
    """Returns monitored roots configuration."""
    return {
        "primary_root": str(PROJECTS_ROOT),
        "extra_roots": [str(p) for p in _extra_roots],
    }


@app.post("/api/config/roots")
async def update_roots(request: Request):
    """Add or remove an extra monitored root directory."""
    global _extra_roots
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    action = data.get("action", "")
    path_str = (data.get("path") or "").strip()
    if not path_str:
        return JSONResponse({"error": "path is required"}, status_code=400)

    p = Path(path_str).expanduser().resolve()

    if action == "add":
        if not p.is_dir():
            return JSONResponse({"error": f"Directório não encontrado: {p}"}, status_code=400)
        if p == PROJECTS_ROOT:
            return JSONResponse({"error": "Essa pasta já é a pasta principal"}, status_code=400)
        if p not in _extra_roots:
            _extra_roots.append(p)
            _save_roots_config()
            _discover()
    elif action == "remove":
        _extra_roots = [r for r in _extra_roots if r != p]
        _save_roots_config()
        _discover()
    else:
        return JSONResponse({"error": "action must be 'add' or 'remove'"}, status_code=400)

    return {
        "primary_root": str(PROJECTS_ROOT),
        "extra_roots": [str(r) for r in _extra_roots],
    }


# Serve static — deve ficar por último para não conflituar com as rotas acima
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")

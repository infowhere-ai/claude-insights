import asyncio
import json
import os
import subprocess
from pathlib import Path

from fastapi import FastAPI, Query, Request
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
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Estado em memória
projects: dict[str, dict] = {}         # project_name -> status dict
_status_paths: dict[str, Path] = {}    # project_name -> path to status.json
_mtimes: dict[str, float] = {}         # path_str -> mtime
_sse_clients: list[asyncio.Queue] = [] # uma queue por cliente SSE


def _discover() -> None:
    """Descobre projectos com .claude/status.json em PROJECTS_ROOT."""
    found: set[str] = set()
    for status_path in PROJECTS_ROOT.glob("*/.claude/status.json"):
        name = status_path.parts[-3]  # .../github-infowhere/<name>/.claude/status.json
        found.add(name)
        if name not in _status_paths:
            _status_paths[name] = status_path

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

        # 3. Para ficheiros untracked ou sem alterações vs HEAD,
        #    usa --no-index para mostrar o ficheiro completo como novo
        if not diff:
            result3 = subprocess.run(
                ["git", "diff", "--no-index", "/dev/null", str(file_path)],
                cwd=str(project_path),
                capture_output=True, text=True, timeout=10,
            )
            # git diff --no-index retorna exit code 1 quando há diferenças (normal)
            diff = result3.stdout.strip()

        return JSONResponse({"diff": diff, "file": str(file_path), "is_new": not result.stdout.strip()})
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


# Serve static — deve ficar por último para não conflituar com as rotas acima
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")

"""Server-Sent Events endpoint."""
import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from claude_monitor import state

router = APIRouter(tags=["sse"])


@router.get("/events")
async def sse_events(request: Request):  # pragma: no cover
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    state._sse_clients.append(queue)

    async def event_generator():
        yield f"data: {json.dumps({'type': 'init', 'projects': state.projects, 'pending_projects': state._pending_projects})}\n\n"
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
                state._sse_clients.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )

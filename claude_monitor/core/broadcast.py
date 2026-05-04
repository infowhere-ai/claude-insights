"""SSE broadcast — send events to all connected clients."""

import asyncio

from claude_monitor import state


def broadcast(data: dict) -> None:
    for q in state._sse_clients:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass

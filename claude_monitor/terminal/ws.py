"""Terminal WebSocket — bridges the claude CLI to the browser via PTY."""

import asyncio
import fcntl
import json
import os
import pty
import shutil
import struct
import termios
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["terminal"])


async def _pty_to_ws(master_fd: int, websocket: WebSocket, alive_ref: list[bool]) -> None:
    """Read bytes from the PTY master_fd and forward them to the WebSocket."""
    loop = asyncio.get_running_loop()
    try:
        while alive_ref[0]:
            data = await loop.run_in_executor(None, lambda: os.read(master_fd, 4096))
            if not data:
                break
            await websocket.send_bytes(data)
    except (OSError, Exception):
        pass
    finally:
        alive_ref[0] = False


async def _ws_to_pty(
    master_fd: int,
    websocket: WebSocket,
    alive_ref: list[bool],
    set_winsize_fn: Callable[[int, int, int], None],
) -> None:
    """Read messages from the WebSocket and write them to the PTY master_fd."""
    try:
        while alive_ref[0]:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            raw = msg.get("bytes") or (msg.get("text", "").encode() if msg.get("text") else None)
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                if obj.get("type") == "resize":
                    set_winsize_fn(master_fd, int(obj["rows"]), int(obj["cols"]))
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
        alive_ref[0] = False


@router.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket):  # pragma: no cover
    await websocket.accept()

    claude_path = shutil.which("claude")
    if not claude_path:
        await websocket.send_bytes(b"\r\n\x1b[31mError: 'claude' not found in PATH\x1b[0m\r\n")
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
    proc = await asyncio.create_subprocess_exec(
        claude_path,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        close_fds=True,
        cwd=str(Path.home()),
    )
    os.close(slave_fd)
    alive_ref: list[bool] = [True]

    t1 = asyncio.create_task(_pty_to_ws(master_fd, websocket, alive_ref))
    t2 = asyncio.create_task(_ws_to_pty(master_fd, websocket, alive_ref, _set_winsize))
    try:
        await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
    finally:
        alive_ref[0] = False
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

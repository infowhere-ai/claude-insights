"""Tests for terminal WebSocket — async subprocess safety and extracted helpers."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestTerminalAsyncSubprocessSafety:
    """
    Verify the terminal handler uses asyncio.create_subprocess_exec,
    not subprocess.Popen.

    subprocess.Popen inside an async function is a synchronous call that
    blocks the event loop during process creation. asyncio.create_subprocess_exec
    is the async-native equivalent and keeps the event loop free.

    Red: would fail if subprocess.Popen were called (create_subprocess_exec
         would never be invoked).
    Green: passes after replacing Popen with create_subprocess_exec.
    """

    def test_terminal_uses_async_create_subprocess_exec(self, app_client):
        mock_proc = MagicMock()
        mock_proc.kill = MagicMock()

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("pty.openpty", return_value=(10, 11)),
            patch("os.close"),
            patch("os.read", side_effect=OSError("pty closed")),
            patch(
                "claude_monitor.terminal.ws.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=mock_proc,
            ) as mock_exec,
        ):
            try:
                with app_client.websocket_connect("/ws/terminal") as ws:
                    ws.close()
            except Exception:
                pass

        mock_exec.assert_awaited_once()
        assert mock_exec.call_args[0][0] == "/usr/bin/claude"

    def test_terminal_does_not_import_subprocess_popen(self):
        """subprocess.Popen must not appear in the terminal module source."""
        import inspect

        import claude_monitor.terminal.ws as ws_module

        source = inspect.getsource(ws_module)
        assert "subprocess.Popen" not in source
        assert "asyncio.create_subprocess_exec" in source


class TestPtyToWs:
    """
    Tests for the extracted _pty_to_ws module-level helper.

    Given: alive_ref=[True], master_fd that returns data then raises OSError.
    When:  _pty_to_ws is called.
    Then:  data is forwarded to websocket and alive_ref is set to False on exit.
    """

    @pytest.mark.asyncio
    async def test_sends_data_from_pty_to_websocket(self):
        """_pty_to_ws reads from master_fd and sends bytes to websocket."""
        from claude_monitor.terminal.ws import _pty_to_ws

        alive_ref = [True]
        websocket = AsyncMock()
        loop = asyncio.get_running_loop()

        call_count = 0

        def fake_read(fd, n):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"hello from pty"
            raise OSError("pty closed")

        with patch("claude_monitor.terminal.ws.os.read", side_effect=fake_read):
            with patch("asyncio.get_running_loop", return_value=loop):
                await _pty_to_ws(10, websocket, alive_ref)

        websocket.send_bytes.assert_awaited_once_with(b"hello from pty")
        assert alive_ref[0] is False

    @pytest.mark.asyncio
    async def test_sets_alive_false_on_empty_read(self):
        """_pty_to_ws sets alive_ref[0]=False when read returns empty bytes."""
        from claude_monitor.terminal.ws import _pty_to_ws

        alive_ref = [True]
        websocket = AsyncMock()
        loop = asyncio.get_running_loop()

        with patch("claude_monitor.terminal.ws.os.read", return_value=b""):
            with patch("asyncio.get_running_loop", return_value=loop):
                await _pty_to_ws(10, websocket, alive_ref)

        websocket.send_bytes.assert_not_awaited()
        assert alive_ref[0] is False

    @pytest.mark.asyncio
    async def test_sets_alive_false_when_already_false(self):
        """_pty_to_ws exits immediately when alive_ref[0] is already False."""
        from claude_monitor.terminal.ws import _pty_to_ws

        alive_ref = [False]
        websocket = AsyncMock()

        await _pty_to_ws(10, websocket, alive_ref)

        websocket.send_bytes.assert_not_awaited()
        assert alive_ref[0] is False


class TestWsToPty:
    """
    Tests for the extracted _ws_to_pty module-level helper.

    Given: alive_ref=[True], websocket messages.
    When:  _ws_to_pty is called.
    Then:  data is written to master_fd; resize messages call set_winsize_fn.
    """

    @pytest.mark.asyncio
    async def test_writes_bytes_to_pty(self):
        """_ws_to_pty writes received bytes to master_fd."""
        from claude_monitor.terminal.ws import _ws_to_pty

        alive_ref = [True]
        websocket = AsyncMock()
        websocket.receive = AsyncMock(
            side_effect=[
                {"type": "websocket.receive", "bytes": b"input data"},
                {"type": "websocket.disconnect"},
            ]
        )
        set_winsize = MagicMock()

        with patch("claude_monitor.terminal.ws.os.write") as mock_write:
            await _ws_to_pty(10, websocket, alive_ref, set_winsize)

        mock_write.assert_called_once_with(10, b"input data")
        assert alive_ref[0] is False

    @pytest.mark.asyncio
    async def test_handles_resize_message(self):
        """_ws_to_pty calls set_winsize_fn on resize JSON messages."""
        from claude_monitor.terminal.ws import _ws_to_pty

        alive_ref = [True]
        resize_msg = json.dumps({"type": "resize", "rows": 40, "cols": 160}).encode()
        websocket = AsyncMock()
        websocket.receive = AsyncMock(
            side_effect=[
                {"type": "websocket.receive", "bytes": resize_msg},
                {"type": "websocket.disconnect"},
            ]
        )
        set_winsize = MagicMock()

        with patch("claude_monitor.terminal.ws.os.write"):
            await _ws_to_pty(10, websocket, alive_ref, set_winsize)

        set_winsize.assert_called_once_with(10, 40, 160)

    @pytest.mark.asyncio
    async def test_sets_alive_false_on_disconnect(self):
        """_ws_to_pty sets alive_ref[0]=False on websocket.disconnect."""
        from claude_monitor.terminal.ws import _ws_to_pty

        alive_ref = [True]
        websocket = AsyncMock()
        websocket.receive = AsyncMock(return_value={"type": "websocket.disconnect"})
        set_winsize = MagicMock()

        await _ws_to_pty(10, websocket, alive_ref, set_winsize)

        assert alive_ref[0] is False

    @pytest.mark.asyncio
    async def test_exits_immediately_when_alive_false(self):
        """_ws_to_pty exits immediately when alive_ref[0] is already False."""
        from claude_monitor.terminal.ws import _ws_to_pty

        alive_ref = [False]
        websocket = AsyncMock()
        set_winsize = MagicMock()

        await _ws_to_pty(10, websocket, alive_ref, set_winsize)

        websocket.receive.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_writes_text_message_as_bytes(self):
        """_ws_to_pty encodes text messages and writes them to master_fd."""
        from claude_monitor.terminal.ws import _ws_to_pty

        alive_ref = [True]
        websocket = AsyncMock()
        websocket.receive = AsyncMock(
            side_effect=[
                {"type": "websocket.receive", "text": "hello"},
                {"type": "websocket.disconnect"},
            ]
        )
        set_winsize = MagicMock()

        with patch("claude_monitor.terminal.ws.os.write") as mock_write:
            await _ws_to_pty(10, websocket, alive_ref, set_winsize)

        mock_write.assert_called_once_with(10, b"hello")

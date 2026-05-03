"""Tests for terminal WebSocket — async subprocess safety."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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

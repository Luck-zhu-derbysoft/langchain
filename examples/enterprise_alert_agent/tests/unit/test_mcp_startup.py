"""MCP connection failures must not block application startup."""

import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.infrastructure.mcp.mcp_service import RemoteMCPClient


class TestMCPStartup(IsolatedAsyncioTestCase):
    async def test_exception_group_releases_startup_waiter(self) -> None:
        client = RemoteMCPClient()
        failure = BaseExceptionGroup("MCP connection failed", [asyncio.CancelledError()])

        with patch.object(client, "_do_connect", side_effect=failure):
            connected = await asyncio.wait_for(client.initialize(), timeout=1)

        self.assertFalse(connected)

    async def test_failed_connection_releases_startup_waiter(self) -> None:
        client = RemoteMCPClient()

        with patch.object(client, "_do_connect", return_value=False):
            connected = await asyncio.wait_for(client.initialize(), timeout=1)

        self.assertFalse(connected)

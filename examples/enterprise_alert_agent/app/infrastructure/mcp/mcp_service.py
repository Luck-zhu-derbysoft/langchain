import logging
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from app.config.settings import settings
from app.infrastructure.fault.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)


class RemoteMCPClient:
    def __init__(self) -> None:
        self._tools_meta: list[dict[str, Any]] = []
        self._initialized = False
        self._circuit_breaker = CircuitBreaker(
            name="mcp",
            failure_threshold=settings.circuit_breaker_failure_threshold,
            recovery_seconds=settings.circuit_breaker_recovery_seconds,
        )

    async def initialize(self) -> bool:
        if self._initialized:
            return True
        try:
            headers: dict[str, str] = {}
            if settings.mcp_api_key:
                headers["X-Access-Key"] = settings.mcp_api_key
            if settings.mcp_cookie:
                headers["Cookie"] = settings.mcp_cookie
            # 发送请求到 MCP 服务，获取工具元信息
            async with (
                create_mcp_http_client(headers=headers) as http_client,
                streamable_http_client(url=settings.mcp_service_url, http_client=http_client) as (
                    read,
                    write,
                    _,
                ),
                ClientSession(read_stream=read, write_stream=write) as session,
            ):
                await session.initialize()
                tools_results = await session.list_tools()
                self._tools_meta = [
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.inputSchema or {},
                        },
                    }
                    for tool in tools_results.tools
                ]
                self._initialized = True
            logger.info("MCP client initialized successfully with %d tools", len(self._tools_meta))
            return True
        except Exception:
            logger.exception("MCP client initialization failed")
            return False

    async def call_tool(self, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        try:
            self._circuit_breaker.before_call()
        except CircuitOpenError:
            logger.warning("MCP circuit open, blocking tool call: %s", tool_name)
            return {
                "status": "error",
                "data": [],
                "row_count": 0,
                "error_code": "circuit_open",
                "message": "MCP circuit is open, tool call blocked",
            }

        headers: dict[str, str] = {}
        if settings.mcp_api_key:
            headers["X-Access-Key"] = settings.mcp_api_key
        if settings.mcp_cookie:
            headers["Cookie"] = settings.mcp_cookie
        # 发送请求到 MCP 服务，获取工具元信息
        try:
            async with (
                create_mcp_http_client(headers=headers) as http_client,
                streamable_http_client(url=settings.mcp_service_url, http_client=http_client) as (
                    read,
                    write,
                    _,
                ),
                ClientSession(read_stream=read, write_stream=write) as session,
            ):
                await session.initialize()
                result = await session.call_tool(name=tool_name, arguments=tool_args)
                content = result.content
                if isinstance(content, list) and content:
                    texts = [
                        getattr(item, "text", str(item)) for item in content if item is not None
                    ]
                    self._circuit_breaker.record_success()
                    return {
                        "status": "success",
                        "data": [{"result": "\n".join(texts)}],
                        "row_count": len(content),
                        "error_code": "",
                        "message": "ok",
                    }
                self._circuit_breaker.record_success()
                return {
                    "status": "success",
                    "data": [],
                    "row_count": 0,
                    "error_code": "",
                    "message": "ok",
                }
        except Exception as e:
            self._circuit_breaker.record_failure()
            logger.exception("MCP client call failed: %s", tool_name)
            return {
                "status": "error",
                "data": [],
                "row_count": 0,
                "error_code": "",
                "message": str(e),
            }

    def get_tools_metadata(self) -> list[dict[str, Any]]:
        return self._tools_meta

    async def close(self) -> None:
        """Release MCP client state."""
        self._tools_meta.clear()
        self._initialized = False

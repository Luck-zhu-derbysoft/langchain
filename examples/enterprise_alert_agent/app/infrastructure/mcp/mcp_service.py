import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx
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
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._call_lock = asyncio.Lock()  # 同一会话不支持并发重入，串行化调用
        self._circuit_breaker = CircuitBreaker(
            name="mcp",
            failure_threshold=settings.circuit_breaker_failure_threshold,
            recovery_seconds=settings.circuit_breaker_recovery_seconds,
        )

    async def initialize(self) -> bool:
        if self._initialized:
            return True
        try:
            # 用 AsyncExitStack 手动管理生命周期，session 在 initialize() 之后继续存活，供 call_tool 复用
            headers: dict[str, str] = {}
            if settings.mcp_api_key:
                headers["X-Access-Key"] = settings.mcp_api_key
            if settings.mcp_cookie:
                headers["Cookie"] = settings.mcp_cookie
            # 发送请求到 MCP 服务，获取工具元信息
            stack = AsyncExitStack()
            http_client = await stack.enter_async_context(
                create_mcp_http_client(
                    headers=headers, timeout=httpx.Timeout(settings.mcp_connect_timeout_seconds)
                )
            )
            read, write, _ = await stack.enter_async_context(
                streamable_http_client(url=settings.mcp_service_url, http_client=http_client)
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream=read, write_stream=write)
            )
            await asyncio.wait_for(
                session.initialize(), timeout=settings.mcp_connect_timeout_seconds
            )

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
            self._session = session
            self._exit_stack = stack
            logger.info("MCP client initialized successfully with %d tools", len(self._tools_meta))
            return True
        except Exception:
            logger.exception("MCP client initialization failed")
            return False

    async def _reconnect(self) -> bool:
        """Tear down a stale session/transport and re-establish a fresh one."""
        await self.close()
        return await self.initialize()

    async def call_tool(
        self, tool_name: str, tool_args: dict[str, Any], *, max_attempts: int = 2
    ) -> dict[str, Any]:
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

        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            if self._session is None or not self._initialized:
                # 会话缺失/已损坏（对端断开、进程重启等）：先重连再重试，而不是直接失败
                if not await self._reconnect():
                    last_error = RuntimeError("MCP session not initialized")
                    break
            try:
                async with self._call_lock:
                    logger.info("MCP call start: tool=%s attempt=%d", tool_name, attempt)
                    result = await asyncio.wait_for(
                        self._session.call_tool(name=tool_name, arguments=tool_args),  # type: ignore[union-attr]
                        timeout=settings.mcp_call_timeout_seconds,
                    )
                    logger.info("MCP call completed: tool=%s", tool_name)
                content = result.content
                self._circuit_breaker.record_success()
                if isinstance(content, list) and content:
                    texts = [
                        getattr(item, "text", str(item)) for item in content if item is not None
                    ]
                    return {
                        "status": "success",
                        "data": [{"result": "\n".join(texts)}],
                        "row_count": len(content),
                        "error_code": "",
                        "message": "ok",
                    }
                return {
                    "status": "success",
                    "data": [],
                    "row_count": 0,
                    "error_code": "",
                    "message": "ok",
                }
            except Exception as e:
                # 会话可能已损坏（比如连接被对端关闭），标记为未初始化，下一轮循环触发重连
                last_error = e
                self._initialized = False
                logger.warning(
                    "MCP call failed (attempt %d/%d): tool=%s err=%s",
                    attempt,
                    max_attempts,
                    tool_name,
                    e,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(0.2 * attempt)

        self._circuit_breaker.record_failure()
        logger.error("MCP client call failed after %d attempt(s): %s", max_attempts, tool_name)
        return {
            "status": "error",
            "data": [],
            "row_count": 0,
            "error_code": "",
            "message": str(last_error),
        }

    def get_tools_metadata(self) -> list[dict[str, Any]]:
        return self._tools_meta

    async def close(self) -> None:
        """Release MCP client state."""
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._session = None
        self._tools_meta.clear()
        self._initialized = False
        self._exit_stack = None

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from app.config.settings import settings
from app.infrastructure.fault.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)


@dataclass
class _CallRequest:
    tool_name: str
    tool_args: dict[str, Any]
    future: "asyncio.Future[dict[str, Any]]" = field(compare=False)


class RemoteMCPClient:
    def __init__(self) -> None:
        self._tools_meta: list[dict[str, Any]] = []
        self._initialized = False
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        # self._call_lock = asyncio.Lock()  # 同一会话不支持并发重入，串行化调用
        self._queue: asyncio.Queue[_CallRequest | None] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._circuit_breaker = CircuitBreaker(
            name="mcp",
            failure_threshold=settings.circuit_breaker_failure_threshold,
            recovery_seconds=settings.circuit_breaker_recovery_seconds,
        )

    async def _do_connect(self) -> bool:
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

    async def initialize(self) -> bool:
        if self._worker_task is not None:
            return self._initialized
        self._queue = asyncio.Queue()
        ready: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._worker_task = asyncio.create_task(self._run(ready))
        return await ready

    async def _run(self, ready: asyncio.Future[bool]) -> None:
        try:
            connected = await self._do_connect()
            if not ready.done():
                ready.set_result(connected)
            if self._queue is None:
                return
            while True:
                request = await self._queue.get()
                if request is None:
                    break
                response = await self._handle_request(request.tool_name, request.tool_args)
                if request.future and not request.future.done():
                    request.future.set_result(response)
        finally:
            await self._shutdown_worker_resources()
            self._worker_task = None

    async def _handle_request(
        self, tool_name: str, tool_args: dict[str, Any], *, max_attempts: int = 2
    ) -> dict[str, Any]:
        for attempt in range(1, max_attempts + 1):
            try:
                if self._session is None or self._exit_stack is None:
                    conn = await self._do_connect()
                    if not conn:
                        raise RuntimeError("Failed to connect to MCP server")  # 同样未被包住
                logger.info("MCP call start: tool=%s attempt=%d", tool_name, attempt)

                result = await asyncio.wait_for(
                    self._session.call_tool(name=tool_name, arguments=tool_args),
                    timeout=settings.mcp_call_timeout_seconds,
                )
                logger.info("MCP call completed: tool=%s", tool_name)
                content = result.content
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
                self._initialized = False
                logger.warning(
                    "MCP call failed (attempt %d/%d): tool=%s err=%s",
                    attempt,
                    max_attempts,
                    tool_name,
                    e,
                )
                if self._session is not None:
                    try:
                        if self._exit_stack is not None:
                            await self._exit_stack.aclose()
                    except Exception as e:
                        logger.warning("Error closing MCP session: %s", e)
                    finally:
                        self._session = None
                        self._exit_stack = None
                if attempt < max_attempts:
                    await asyncio.sleep(0.2 * attempt)
        return {"status": "error", "tool_name": tool_name, "tool_args": tool_args}

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
        if self._queue is None or self._worker_task is None:
            self._circuit_breaker.record_failure()
            return {
                "status": "error",
                "data": [],
                "row_count": 0,
                "error_code": "not_initialized",
                "message": "MCP service is not initialized",
            }
        function: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        await self._queue.put(_CallRequest(tool_name, tool_args, function))
        result = await function
        if result.get("status") == "success":
            self._circuit_breaker.record_success()
        else:
            self._circuit_breaker.record_failure()

        return result

    def get_tools_metadata(self) -> list[dict[str, Any]]:
        return self._tools_meta

    async def _shutdown_worker_resources(self) -> None:
        try:
            if self._exit_stack is not None:
                await self._exit_stack.aclose()
        except Exception as e:
            logger.warning("Error closing exit stack: %s", e)
        finally:
            self._session = None
            self._tools_meta = []
            self._initialized = False
            self._exit_stack = None

    async def close(self) -> None:
        if self._queue is None or self._worker_task is None:
            return
        await self._queue.put(None)
        await self._worker_task

    async def _do_close(self) -> None:
        if self._queue is None:
            return
        await self._queue.put(None)

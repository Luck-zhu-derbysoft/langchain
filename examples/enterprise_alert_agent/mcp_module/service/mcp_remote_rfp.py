import asyncio
import json
import time
from typing import Any, cast

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
from mcp.shared._httpx_utils import create_mcp_http_client

from app.config.settings import settings


def _ok(data: Any, latency_ms: int = 0) -> dict[str, Any]:
    return {
        "status": "success",
        "error_code": "",
        "message": "ok",
        "latency_ms": latency_ms,
        "row_count": 0,
        "data": data or {},
    }


def _error(message: str, error_code: str = "") -> dict[str, Any]:
    return {
        "status": "error",
        "error_code": error_code,
        "message": message,
        "latency_ms": 0,
        "row_count": 0,
        "data": [],
    }


def register_mcp_remote_rfp_service(service: FastMCP) -> None:
    @service.tool()
    def call_remotemcp_tool(
        upstream_name: str,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_id: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        if not settings.mcp_upstream_enabled:
            return _error("远程 MCP 调用功能未启用", error_code="remote_mcp_disabled")
        if not isinstance(tool_args, dict):
            return _error("tool_args 必须是一个字典", error_code="invalid_tool_args")
        #格式:[{"name":"rfp_gateway","url":"http://localhost:8081/dmatch-main/mcp","headers":{"X-Access-Key":"your-key","Cookie":"r_token=your-token"},"allowed_tools":["queryRfpHotelBids"]}]
        upstreams = _load_upstreams()
        upstream = upstreams.get(upstream_name, {})
        # 检查上游 NAME存在
        if not upstream:
            return _error("未找到指定远程 MCP 服务", error_code="upstream_not_found")
        allowed_tools = upstream.get("allowed_tools", [])
        if tool_name not in allowed_tools:
            return _error("该工具不允许被远程 MCP 调用", error_code="tool_not_allowed")
        upstream_url = upstream.get("url")
        headers = upstream.get("headers", {})
        try:
            result = asyncio.run(
                    asyncio.wait_for(
                        call_remote_mcp(
                            upstream_url=cast(str, upstream_url),
                            tool_name=tool_name,
                            tool_args=tool_args,
                            headers=headers,
                        ),
                        timeout=settings.mcp_upstream_timeout_seconds,
                        ) # 创建一个异步任务
                    )
            latency_ms = int((time.perf_counter() - started) * 1000)
            return _ok(
                {
                    "upstream_name": upstream_name,
                    "tool_name": tool_name,
                    "result": result,
                },
                latency_ms=latency_ms,
            )
        except Exception as e:
            return _error(f"远程 MCP 调用失败: {e!s}", error_code="remote_mcp_call_failed")
        finally:
            latency_ms = int((time.perf_counter() - started) * 1000)


def _load_upstreams() -> dict[str, dict[str, Any]]:
    upstreams = []
    try:
        upstreams = json.loads(settings.mcp_upstreams_json)
        if not isinstance(upstreams, list):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for upstream in upstreams:
            if not isinstance(upstream, dict):
                continue
            name = upstream.get("name")
            url = upstream.get("url")
            headers = upstream.get("headers", {})
            allowed_tools = upstream.get("allowed_tools", [])
            if name:
                result[name] = {
                    "url": url,
                    "headers": headers,
                    "allowed_tools": allowed_tools,
                }
        return result
    except Exception:
        return {}


async def call_remote_mcp(
    upstream_url: str,
    tool_name: str,
    tool_args: dict[str, Any],
    headers: dict[str, str],
) -> Any:
    """调用远程 MCP 服务"""

    async with (
        create_mcp_http_client(headers=headers or {}) as http_client,
        streamable_http_client(url=upstream_url, http_client=http_client) as (read, write, _),
        ClientSession(read_stream=read, write_stream=write) as session,
    ):
        await session.initialize()
        return await session.call_tool(name=tool_name, arguments=tool_args)

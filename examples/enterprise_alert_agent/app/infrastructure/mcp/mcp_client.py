import logging
import threading
from collections.abc import Callable
from typing import Any

from app.config.settings import settings
from app.infrastructure.mcp.mcp_service import RemoteMCPClient

logger = logging.getLogger(__name__)

# 全局客户端实例
_mcp_client: RemoteMCPClient | None = None
_init_lock: Any = threading.Lock()


async def async_init_mcp() -> bool:
    global _mcp_client
    if _mcp_client is not None:
        logger.info("MCP already initialized")
        return True
    if not settings.mcp_enabled:
        logger.info("MCP disabled by configuration")
        return False
    with _init_lock:
        if _mcp_client is not None:
            logger.info("MCP already initialized by another thread")
            return True
        _mcp_client = RemoteMCPClient()
    try:
        ok = await _mcp_client.initialize()
        if not ok:
            logger.warning("MCP initialize failed: %s", settings.mcp_service_url)
            _mcp_client = None
            return False
        logger.info("MCP initialized successfully")
        return True
    # 分层捕获异常，精细化日志
    except TimeoutError:
        logger.error("MCP connect timeout, service_url=%s", settings.mcp_service_url)
        _mcp_client = None
        return False
    except ConnectionError as exc:
        logger.error(
            "MCP service connection refused, url=%s err=%s",
            settings.mcp_service_url,
            str(exc),
        )
        _mcp_client = None
        return False
    except Exception:
        logger.exception("Failed to init MCP client, config url=%s", settings.mcp_service_url)
        _mcp_client = None
        return False


async def async_close_mcp() -> None:
    """Close the shared MCP client and release its session resources."""
    global _mcp_client
    with _init_lock:
            client = _mcp_client
            _mcp_client = None
    if client is not None:
        await client.close()



def get_tools_metadata() -> list:
    # {
    #                         "type": "function",
    #                         "function": {
    #                             "name": tool.name,
    #                             "description": tool.description,
    #                             "parameters": tool.inputSchema or {},
    #                         },
    #                     }
    if _mcp_client is None:
        return []
    return _mcp_client.get_tools_metadata()


async def async_get_tool_map() -> dict[str, Callable[..., Any]]:
    """Build asynchronous MCP tool functions for async callers."""
    if _mcp_client is None:
        return {}
    return {
        tool["function"]["name"]: _make_async_remote_tool_func(
            _mcp_client, tool["function"]["name"]
        )
        for tool in _mcp_client.get_tools_metadata()
    }


# LLM输出工具调用
#         ↓
# name = "queryRfpHotelBids" , arguments = {city:"上海"}
#         ↓
# func = tool_map[name]
#         ↓
# func(**arguments) → tool_function(city="上海")
#         ↓
# 闭包携带tool_name + kwargs → _mcp_client.call_tool(tool_name, kwargs)
#         ↓
# 发起HTTP/SSE请求调用Java MCP服务
#         ↓
# 结果原路逐层返回


def _make_async_remote_tool_func(mcp_client: RemoteMCPClient, tool_name: str):
    """Generate an async MCP tool without nested event loops or thread pools."""

    async def tool_function(**kwargs: Any) -> Any:
        return await mcp_client.call_tool(tool_name, kwargs)

    return tool_function

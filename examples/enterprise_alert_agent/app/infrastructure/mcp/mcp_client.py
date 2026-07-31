

import asyncio
import concurrent.futures
import logging
from typing import Any

from app.config.settings import settings
from app.infrastructure.mcp.mcp_service import RemoteMCPClient

logger = logging.getLogger(__name__)

#全局客户端实例
_mcp_client: RemoteMCPClient | None = None
_init_lock: asyncio.Lock = asyncio.Lock()  # ← 在模块级定义


async def async_init_mcp() -> bool:
    global _mcp_client
    if _mcp_client is not None:
        logger.info("MCP already initialized")
        return True
    if not settings.mcp_enabled:
        logger.info("MCP disabled by configuration")
        return False
    async with _init_lock:
        if _mcp_client is not None:
            logger.info("MCP already initialized by another thread")
            return True
        try:
            _mcp_client = RemoteMCPClient()
            ok = await _mcp_client.initialize()
            if not ok:
                logger.warning("MCP initialize failed: %s", settings.mcp_service_url)
                return False
            logger.info("MCP initialized successfully")
            return True
        # 分层捕获异常，精细化日志
        except TimeoutError:
            logger.error("MCP connect timeout, service_url=%s", settings.mcp_service_url)
            return False
        except ConnectionError as e:
            logger.error("MCP service connection refused, url=%s err=%s", settings.mcp_service_url, str(e))
            return False
        except Exception:
            # 兜底捕获，打印完整堆栈便于排错
            logger.exception("Failed to init MCP client, config url=%s", settings.mcp_service_url)
            return False

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

def get_tool_map() -> dict:
    if _mcp_client is None:
        return {}
    tool_map = {}
    for tool in _mcp_client.get_tools_metadata():
        name = tool["function"]["name"]
        tool_map[name] = _make_remote_tool_func(_mcp_client, name)
    return tool_map
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
def _make_remote_tool_func(mcp_client: RemoteMCPClient, tool_name: str):
    """生成闭包函数，固化mcp_client tool_name"""
    def tool_function(**kwargs: Any) -> Any:
        try:
            loop = asyncio.get_running_loop()
            running = loop.is_running()
        except RuntimeError:
            running = False
        if running:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(
                    lambda: asyncio.run(mcp_client.call_tool(tool_name, kwargs))
                ).result()
        else:
            return asyncio.run(mcp_client.call_tool(tool_name, kwargs))

    return tool_function

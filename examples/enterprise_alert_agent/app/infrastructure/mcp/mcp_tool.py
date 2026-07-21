

import asyncio
import logging

from app.config.settings import settings
from app.infrastructure.mcp.mcp_adapt import MCPToolAdapter
from app.infrastructure.mcp.mcp_client import MCPClient

logger = logging.getLogger(__name__)

#全局客户端实例
_mcp_client: MCPClient | None = None
_map_adapter:MCPToolAdapter | None = None
_init_lock: asyncio.Lock = asyncio.Lock()  # ← 在模块级定义


async def async_init_mcp() -> bool:
    global _mcp_client, _map_adapter
    if _map_adapter is not None:
        logger.info("MCP already initialized")
        return True
    if not settings.mcp_enabled:
        logger.info("MCP disabled by configuration")
        return False
    async with _init_lock:
        if _map_adapter is not None:
            logger.info("MCP already initialized by another thread")
            return True
        try:
            _mcp_client = MCPClient(service_url=settings.mcp_service_url,
                                    timeout=settings.mcp_timeout,
                                    api_key=settings.mcp_api_key)
            ok = await _mcp_client.initialize()
            if not ok:
                logger.warning("MCP initialize failed: %s", settings.mcp_service_url)
                return False
            _map_adapter = MCPToolAdapter(_mcp_client)
            _map_adapter.initialize_tools()
            logger.info("MCP initialized successfully")
            return True
        # 分层捕获异常，精细化日志
        except asyncio.TimeoutError:
            logger.error("MCP connect timeout, service_url=%s", settings.mcp_service_url)
            return False
        except ConnectionError as e:
            logger.error("MCP service connection refused, url=%s err=%s", settings.mcp_service_url, str(e))
            return False
        except Exception as e:
            # 兜底捕获，打印完整堆栈便于排错
            logger.error("Failed to init MCP client, config url=%s", settings.mcp_service_url, exc_info=True)
            return False

def get_tool_map() -> dict:
    if _map_adapter is None:
        return {}
    return _map_adapter.get_tool_mapping()

def get_tools_metadata() -> list:
    if _map_adapter is None:
        return []
    return _map_adapter.get_tools_metadata()

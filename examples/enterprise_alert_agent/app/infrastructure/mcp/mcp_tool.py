

import asyncio
from typing import Callable, Dict

from app.config.settings import settings
from app.infrastructure.mcp.mcp_adapt import MCPToolAdapter
from app.infrastructure.mcp.mcp_client import MCPClient

#全局客户端实例
_mcp_client: MCPClient | None = None
_map_adapter:MCPToolAdapter | None = None


def init_mcp() ->bool:
    global _mcp_client, _map_adapter
    # 这里可以进行 MCP 客户端的初始化，例如创建 MCPClient 实例、连接 MCP 服务器等
    if _map_adapter is not None:
        print("✅ MCP 客户端已存在，跳过初始化")
        return True
    if not settings.mcp_enabled:
        return False
    try:
        _mcp_client = MCPClient(service_url=settings.mcp_service_url,
                                timeout=settings.mcp_timeout,
                                api_key=settings.mcp_api_key)
        loop = asyncio.new_event_loop()
        mcp_client_init = loop.run_until_complete(_mcp_client.initialize())
        loop.close()
        if not mcp_client_init:
            print(f"❌ MCP 客户端初始化失败 ，当前配置地址: {settings.mcp_service_url}")

            return False
        print("✅ MCP 客户端初始化成功")

        _map_adapter = MCPToolAdapter(_mcp_client)
        _map_adapter.initialize_tools()
        print("✅ MCP 工具适配器初始化成功")
        return True
    except Exception as e:
        print(f"❌ 初始化 MCP 客户端失败: {e}，当前配置: {settings.mcp_service_url}")
        return False


def get_tool_map() -> dict:
    if _map_adapter is None:
        return {}
    return _map_adapter.get_tool_mapping()

def get_tools_metadata() -> list:
    if _map_adapter is None:
        return []
    return _map_adapter.get_tools_metadata()

# 导出给外部使用
MCP_TOOLS_METADATA: list = []  # MCP工具的元数据列表
MCP_SKILL_MAP: Dict[str, Callable] = {}  # 动态初始化时填充

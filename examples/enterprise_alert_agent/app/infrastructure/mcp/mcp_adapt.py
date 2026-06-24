

import asyncio
import logging
from typing import Any, Callable, Dict, List, cast

from app.infrastructure.mcp.mcp_client import MCPClient
import concurrent.futures

logger = logging.getLogger(__name__)


class MCPToolAdapter:
    def __init__(self, mcp_tool: MCPClient) -> None:
        self.mcp_tool = mcp_tool
        self._tools_map: Dict[str, Callable] = {}

    def initialize_tools(self) -> None:
        # 获取工具列表并存储到适配器的工具映射中
        for tool in self.mcp_tool._tools_map.values():
            self._tools_map[tool.name] = self._create_tool_function(tool.name)


    def _create_tool_function(self, tool_name: str) -> Callable:
        def tool_function(**kwargs) -> Dict[str, Any]:
            # 这里可以根据工具的输入参数构造 MCP 协议请求并发送到 MCP 服务器
            # 解析响应并返回结果
            try:
                try:
                    loop = asyncio.get_running_loop()
                    loop_is_running = loop.is_running()
                except RuntimeError:
                    loop = None
                    loop_is_running = False

                if loop_is_running:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(
                            lambda: asyncio.run(self.mcp_tool.call_tool(tool_name, kwargs)))
                        result  =future.result()
                else:
                    result = asyncio.run(
                        self.mcp_tool.call_tool(tool_name, kwargs))

                if result.success:
                    payload = result.content if isinstance(result.content, dict) else {}
                    if payload.get("status") in ("success", "error") and "data" in payload:
                        return cast(Dict[str, Any], payload)
                    normalized_data = [payload] if isinstance(payload, dict) else []
                    return cast(Dict[str, Any],{
                        "status": "success",
                        "error_code": "",
                        "row_count": len(normalized_data),
                        "data": normalized_data,
                        "message": "MCP payload normalized",
                    })
                else:
                    return cast(Dict[str, Any],{
                        "status": "error",
                        "error_code": "MCP_TOOL_ERROR",
                        "message": result.error or "MCP tool call failed",
                        "data": [],
                    })

            except Exception as e:
                logger.exception("MCP tool %s execution failed: %s", tool_name, e)
                return cast(Dict[str, Any],{
                    "status": "error",
                    "error_code": "MCP_EXECUTION_ERROR",
                    "message": f"MCP tool execution failed: {str(e)}",
                    "data": [],
                    })
        return tool_function

    def get_tools_metadata(self) -> List[Dict[str, Any]]:
        """获取 Function calling 格式的元数据"""
        return self.mcp_tool.get_tools_metadata()

    def get_tool_mapping(self) -> Dict[str, Callable]:
        """获取工具名称到函数的映射"""
        return self._tools_map

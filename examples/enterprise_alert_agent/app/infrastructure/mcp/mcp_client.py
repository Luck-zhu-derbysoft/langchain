

import json
from typing import Any, Dict, List, Optional

from attr import dataclass
import httpx


@dataclass
class MCPTool:
    """
    A class representing a tool provided by the MCP API.
    """
    name: str
    description: str
    input_schema: Dict[str, Any]
    # 其他工具相关属性

@dataclass
class MCPResult:
    """
    A class representing the result of an operation with the MCP API.
    """
    success: bool
    content: str | Dict[str, Any]
    error: Optional[str] = None



class MCPClient:
    """
    A client for interacting with the MCP (Model Context Protocol) API.
    """

    def __init__(self, service_url: str, timeout: int = 30, api_key: str = "") -> None:
        self.service_url = service_url
        self.timeout = timeout
        self.api_key = api_key
        self._tools_map: Dict[str, MCPTool] ={}  # 内部使用，记录调用过的工具列表
        self._session_id: Optional[str] = None  # 可选的会话 ID，用于保持上下文

    async def initialize(self) -> bool:
        """
        Initialize the MCP client, e.g., by checking connectivity or authentication.
        """
        try:
            tools = await self.get_tool_list()
            if not tools:
                print("⚠️ MCP 服务器没有提供任何工具")
                return False
            self._tools_map = {tool.name: tool for tool in tools}
            return True
        except Exception as e:
            print(f"❌ 初始化 MCP 客户端失败: {e}")
            return False

    async def get_tool_list(self) -> list[MCPTool]:
        """
        获取 MCP 服务器提供的所有工具
        """
        # 构造 MCP 协议请求并发送到 MCP 服务器，解析响应并返回工具列表
        try:
            await self._ensure_session()
            # 这里是示例代码，实际实现需要根据 MCP 协议规范构造请求并处理响应
            payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }

            # 这里示例用 HTTP POST，实际可改为 WebSocket/stdio 等传输方式
            headers ={**self._base_headers(), "mcp-session-id": self._session_id or ""}
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.service_url, json=payload, headers=headers)
                response.raise_for_status()
                data = self._extract_tools_from_response(response.text)
                # 解析 data 获取工具列表
                tools = []
                for tool_info in data.get("result", {}).get("tools", []):
                    tool = MCPTool(
                        name=tool_info["name"],
                        description=tool_info.get("description", ""),
                        input_schema=tool_info.get("inputSchema", {}),
                    )
                    tools.append(tool)
                return tools
        except Exception as e:
            print(f"❌ 获取 MCP 工具列表失败: {e}")
            return []

    async def call_tool(self, tool_name: str, input_data: Dict[str, Any]) -> MCPResult:
        """
        调用 MCP 服务器上的指定工具，并返回结果
        """
        try:
            await self._ensure_session()
            payload = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": input_data,
                },
            }
            headers ={**self._base_headers(), "mcp-session-id": self._session_id or ""}
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.service_url, json=payload, headers=headers)
                response.raise_for_status()
                data = self._extract_tools_from_response(response.text)
                if "error" in data:
                    return MCPResult(success=False, content="", error=data["error"]["message"])
                else:
                    raw_result = data.get("result", {})
                    payload = self._extract_tool_payload(raw_result)
                    return MCPResult(success=True, content=payload)
        except Exception as e:
            print(f"❌ 调用 MCP 工具失败: {e}")
            return MCPResult(success=False, content="", error=str(e))

    def get_tools_metadata(self) -> List[Dict[str, Any]]:
        """
        获取 MCP 服务器上所有工具的元信息，供外部使用
        """
        tools_metadata = []
        for tool in self._tools_map.values():
            tools_metadata.append({
                "type": "function",
                "function":{
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                }
            })
        return tools_metadata

    def _base_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _extract_tools_from_response(response_text: str) -> Dict[str, Any]:
       # 兼容 FastMCP streamable-http 返回的 SSE 格式
        if response_text.lstrip().startswith("{"):
           return json.loads(response_text)
        data_lines=[]
        for line in response_text.splitlines():
                if line.startswith("data: "):
                 data_lines.append(line[6:].strip())
        if not data_lines:
            raise ValueError(f"Invalid MCP response body: {response_text[:200]}")
        return json.loads(data_lines[-1])

    @staticmethod
    def _extract_tool_payload(raw_result: Dict[str, Any]) -> Dict[str, Any]:
    # 1) 优先 structuredContent（部分 FastMCP 版本会返回）
        structured = raw_result.get("structuredContent")
        if isinstance(structured, dict):
                if isinstance(structured.get("result"), dict):
                    return structured["result"]
                return structured
        content = raw_result.get("content")
        if isinstance(content, list):
            # 2) 兼容部分 FastMCP 版本直接返回 content 数组的情况
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if not isinstance(text, str):
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    if isinstance(parsed.get("result"), dict):
                        return parsed["result"]
                return parsed
        return raw_result if isinstance(raw_result, dict) else {}

    async def _ensure_session(self) -> bool:
            if self._session_id:
                return True

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "enterprise-alert-agent", "version": "0.1.0"},
                },
            }

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.service_url, json=payload, headers=self._base_headers())
                resp.raise_for_status()

                self._session_id = resp.headers.get("mcp-session-id")
                if not self._session_id:
                    raise RuntimeError("Missing mcp-session-id from initialize response")

                # 解析 initialize 返回（触发格式校验）
                _ = self._extract_tools_from_response(resp.text)

                # 发送 initialized 通知
                notify_payload = {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
                notify_headers = {**self._base_headers(), "mcp-session-id": self._session_id}
                notify_resp = await client.post(self.service_url, json=notify_payload, headers=notify_headers)
                # FastMCP 一般返回 202，无 body
                if notify_resp.status_code not in (200, 202):
                    notify_resp.raise_for_status()

            return True


"""Answer runtime capability inventory questions without going through the LLM.

The LLM cannot be trusted to faithfully report structured runtime facts (e.g. it may
reclassify or deny MCP-provided tools despite them being explicitly tagged as such in
the runtime capability catalog). Queries that ask about registered services/tools/agents
are therefore answered deterministically from `get_runtime_capabilities()`.
"""

from app.infrastructure.capabilities.runtime_catalog import (
    RuntimeCapability,
    get_runtime_capabilities,
)

_INVENTORY_WORDS = (
    "有哪些",
    "什么服务",
    "什么工具",
    "哪些工具",
    "已注册",
    "注册了",
    "已连接",
    "可用",
    "支持",
    "能力",
    "功能",
    "列表",
    "数量",
    "几个",
    "多少",
)
_RESOURCE_WORDS = ("服务", "工具", "agent", "集成", "能力", "插件", "mcp")


def matches(query: str) -> bool:
    normalized = (query or "").strip().lower()
    if not normalized:
        return False
    return any(word in normalized for word in _INVENTORY_WORDS) and any(
        word in normalized for word in _RESOURCE_WORDS
    )


def render(capabilities: list[RuntimeCapability]) -> str:
    if not capabilities:
        return "当前未发现已注册的运行时能力。"

    services = [item for item in capabilities if item.kind == "service"]
    tools = [item for item in capabilities if item.kind == "tool"]
    agents = [item for item in capabilities if item.kind == "agent"]
    integrations = [item for item in capabilities if item.kind == "integration"]

    lines = ["当前已注册的运行时能力如下（来自运行时能力目录，非知识库推断）："]

    for service in services:
        endpoint = f"（{service.endpoint}）" if service.endpoint else ""
        lines.append(f"- 服务: {service.name} [{service.provider}]{endpoint}")

    for tool in tools:
        description = f"：{tool.description}" if tool.description else ""
        lines.append(f"- 工具: {tool.name} [{tool.provider}]{description}")

    for agent in agents:
        lines.append(f"- Agent: {agent.name} [{agent.provider}]")

    for integration in integrations:
        lines.append(f"- 集成: {integration.name} [{integration.provider}]")

    mcp_tool_count = sum(1 for tool in tools if tool.provider == "mcp")
    lines.append(
        f"\n当前支持调用的 MCP 服务数量: {1 if any(s.provider == 'mcp' for s in services) else (1 if mcp_tool_count else 0)}"
    )
    lines.append(
        "说明：条目的 provider 字段是运行时确认的事实来源标记，"
        "不会因为底层实现方式（如 REST 封装）、命名风格或知识库未记载而被重新分类或否定。"
    )
    return "\n".join(lines)


def answer_if_matched(query: str) -> str | None:
    if not matches(query):
        return None
    return render(get_runtime_capabilities())

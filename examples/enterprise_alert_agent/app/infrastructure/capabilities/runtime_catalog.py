from dataclasses import asdict, dataclass
from typing import Any

from app.config.settings import settings
from app.infrastructure.agent.agent_registry import AgentRegistry
from app.infrastructure.mcp.mcp_client import get_tools_metadata, is_mcp_initialized
from app.infrastructure.skill.registry import skill_registry


@dataclass(frozen=True)
class RuntimeCapability:
    name: str
    kind: str  # tool | service | agent | integration
    provider: str  # mcp | http | a2a | local | plugin
    description: str = ""
    endpoint: str = ""
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("endpoint", None)
        return data


def get_mcp_capabilities() -> list[RuntimeCapability]:
    if not settings.mcp_enabled or not is_mcp_initialized():
        return []
    tools = [
        RuntimeCapability(
            name=str(item["function"]["name"]),
            kind="tool",
            provider="mcp",
            description=str(item["function"].get("description") or ""),
            metadata={"discovered_by": "mcp.session.list_tools"},
        )
        for item in get_tools_metadata()
        if item.get("function", {}).get("name")
    ]
    if not settings.mcp_service_url:
        return tools
    service = RuntimeCapability(
        name="MCP service",
        kind="service",
        provider="mcp",
        endpoint=settings.mcp_service_url,
        description="通过 MCP 协议发现并提供工具的服务。",
        metadata={"discovered_by": "mcp.session.initialize"},
    )
    return [service, *tools]


def get_agent_capabilities(
    agent_registry: AgentRegistry | None,
) -> list[RuntimeCapability]:
    if agent_registry is None:
        return []

    return [
        RuntimeCapability(
            name=agent.agent_id,
            kind="agent",
            provider="local",
            description=", ".join(agent.capabilities),
            metadata={
                "display_name": agent.display_name,
                "supported_tools": agent.supported_tools,
            },
        )
        for agent in agent_registry.list_agents()
    ]


def get_skill_capabilities() -> list[RuntimeCapability]:
    capabilities: list[RuntimeCapability] = []
    for item in skill_registry.metadata():
        skill_metadata = item.get("metadata")
        if not isinstance(skill_metadata, dict):
            skill_metadata = {}
        function_metadata = skill_metadata.get("function")
        if not isinstance(function_metadata, dict):
            function_metadata = {}
        name = item.get("name") or function_metadata.get("name")
        if not name:
            continue
        capabilities.append(
            RuntimeCapability(
                name=str(name),
                kind="tool",
                provider="local",
                description=str(
                    function_metadata.get("description") or skill_metadata.get("description") or ""
                ),
                metadata={"discovered_by": "local.skill_registry"},
            )
        )
    return capabilities


def get_runtime_capabilities(
    agent_registry: AgentRegistry | None = None,
) -> list[RuntimeCapability]:
    mcp_capabilities = get_mcp_capabilities()
    mcp_tool_names = {item.name for item in mcp_capabilities if item.kind == "tool"}
    local_capabilities = [
        item for item in get_skill_capabilities() if item.name not in mcp_tool_names
    ]
    agent_capabilities = get_agent_capabilities(agent_registry)
    return [*mcp_capabilities, *local_capabilities, *agent_capabilities]

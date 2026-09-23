from dataclasses import asdict, dataclass
from typing import Any

from app.config.settings import settings
from app.infrastructure.mcp.mcp_client import get_tools_metadata


@dataclass(frozen=True)
class RuntimeCapability:
    name: str
    kind: str  # tool | service | agent | integration
    provider: str  # mcp | http | a2a | local | plugin
    description: str = ""
    endpoint: str = ""
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_mcp_capabilities() -> list[RuntimeCapability]:
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
    if not (settings.mcp_enabled and settings.mcp_service_url):
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


def get_runtime_capabilities() -> list[RuntimeCapability]:
    return [
        *get_mcp_capabilities(),
        # *get_http_capabilities(),
        # *get_a2a_capabilities(),
        # *get_local_capabilities(),
    ]

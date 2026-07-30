from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from app.config.settings import settings
from mcp_module.service.mcp_remote_rfp import register_mcp_remote_rfp_service
from mcp_module.service.mysql_service import register_mysql_service

ServiceRegistrar = Callable[[FastMCP], None]

SERVICE_REGISTRY: dict[str, ServiceRegistrar] = {
    "mysql": register_mysql_service,
    "remote_rfp": register_mcp_remote_rfp_service,
}


def create_mcp_server() -> FastMCP:
    mcp = FastMCP(
        "enterprise-alert-mysql-mcp",
        host=getattr(settings, "mcp_host", "127.0.0.1"),
        port=getattr(settings, "mcp_port", 3000),
        streamable_http_path=getattr(settings, "mcp_path", "/mcp"),
    )
    return mcp


def registry_enable_services(mcp: FastMCP) -> None:
    enable_services = getattr(settings, "mcp_enable_services", [])
    for service_name in enable_services:
        registrar = SERVICE_REGISTRY.get(service_name)
        print(f"加载的工具: {service_name}")
        if registrar:
            registrar(mcp)
        else:
            print(
                f"⚠️ 警告: 配置中指定的 MCP 服务 '{service_name}' 不在 SERVICE_REGISTRY 中，已忽略。"
            )


def main() -> None:
    mcp_server = create_mcp_server()
    registry_enable_services(mcp_server)
    mcp_server.run(transport="streamable-http")


if __name__ == "__main__":
    main()

"""Database skill metadata placeholder for MCP-provided database tools."""

from typing import Any

# Skill 层只保留“怎么用”的提示，不做真实执行
MYSQL_TOOL_USER_PROMPT = (
    "当问题需要实时业务时，优先调用可用的 MCP 数据库工具。"
    "SQL 必须为只读语句（SELECT/SHOW/DESC/EXPLAIN）。\n"
    "优先加LIMIT限制返回行数，默认10行，最大50行。\n"
    "若不确定字段或表结构，可先使用 SHOW TABLES / DESCRIBE table_name。"
)


# 为了避免与 MCP 工具重复注册，这里不再向 LLM 注册同名本地函数
DB_TOOLS_METADATA: list[dict[str, Any]] = []
# 严格分层：本地 skill 不再提供执行函数
DB_SKILL_MAP: dict[str, Any] = {}

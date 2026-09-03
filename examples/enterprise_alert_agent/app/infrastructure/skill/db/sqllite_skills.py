# skills/db_skills.py
import os
import sqlite3
from typing import Any

from app.config.settings import settings

# 1. 声明给大模型的 Tool 元数据（JSON Schema）
DB_TOOLS_METADATA = [
    {
        "type": "function",
        "function": {
            "name": "query_local_database",
            "description": "执行只读 SQL 语句查询本地 SQLite 数据库，用于获取历史告警记录、资产配置（CMDB）等。注意：本工具仅支持 SELECT 查询。"
            "【特别注意】：SQLite 的 sqlite_master 表不包含各表的真实行数！"
            "如果需要查询多个表的业务数据量（row_count），请勿对 sqlite_master 使用 COUNT(*)，"
            "请分别对各个具体业务表执行 SELECT COUNT(*) 并使用 UNION ALL 进行拼接。",
            ",parameters": {
                "type": "object",
                "properties": {
                    "sql_query": {
                        "type": "string",
                        "description": "标准的只读 SQL 语句。例如: 'SELECT * FROM alert_history WHERE status=\"active\" LIMIT 5;'",
                    }
                },
                "required": ["sql_query"],
            },
        },
    }
]


# 2. 编写具备防御性编程的数据库查询函数
def query_local_database(sql_query: str) -> dict[str, Any]:
    """技能执行体：安全地查询本地 SQLite 数据库，由 Agent 通过技能注册表调用。"""
    # 严格的安全机制 1：静态关键字拦截，防止大模型幻觉生成了 DROP/DELETE/UPDATE 等语句
    forbidden_keywords = [
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "create",
        "replace",
        "truncate",
    ]
    clean_query = sql_query.strip().lower()

    if not clean_query.startswith("select"):
        return {
            "status": "error",
            "message": "安全防御限制：本接口仅支持以 SELECT 开头的查询语句。",
        }

    for kw in forbidden_keywords:
        if kw in clean_query:
            return {
                "status": "error",
                "message": f"安全防御限制：查询语句中包含禁止的敏感关键字 [{kw}]。",
            }

    # 确保数据库目录和文件存在（避免连接时自动创建空文件）
    if not os.path.exists(settings.sqlite_path):
        return {"status": "error", "message": f"数据库文件未找到，路径: {settings.sqlite_path}"}

    conn = None
    try:
        # 严格的安全机制 2：使用 SQLite URI 模式强制以纯只读（mode=ro）方式打开数据库
        # 这确保了即使大模型绕过了上面的关键字拦截，底层数据库引擎也会拒绝任何写操作！
        db_uri = f"file:{settings.sqlite_path}?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True)
        cursor = conn.cursor()

        cursor.execute(sql_query)

        # 动态获取列名，组装成 Key-Value 字典形式返回给大模型，方便其理解语义
        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()

        results = [dict(zip(columns, row)) for row in rows]

        cursor.close()
        return {"status": "success", "count": len(results), "data": results}

    except sqlite3.OperationalError as e:
        return {"status": "error", "message": f"数据库权限或操作异常（可能触发了只读保护）: {e!s}"}
    except Exception as e:
        return {"status": "error", "message": f"执行 SQL 查询时发生未知错误: {e!s}"}
    finally:
        if conn:
            conn.close()


# 技能路由字典映射
DB_SKILL_MAP = {"query_local_database": query_local_database}

from app.infrastructure.skill.registry import SkillDescriptor, skill_registry

skill_registry.register(
    SkillDescriptor(
        name="query_local_database",
        func=query_local_database,  # 占位函数，实际调用通过 skills_map 获取
        metadata=DB_TOOLS_METADATA[0],
        enabled=True,
    )
)

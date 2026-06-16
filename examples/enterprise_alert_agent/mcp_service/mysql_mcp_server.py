from typing import Any, Dict

import pymysql
from mcp.server.fastmcp import FastMCP
from pymysql.cursors import DictCursor

from app.config.settings import settings

mcp = FastMCP(
    "enterprise-alert-mysql-mcp",
    host = getattr(settings, "mcp_host", "127.0.0.1"),
    port = getattr(settings, "mcp_port", 3000),
    streamable_http_path=getattr(settings, "mcp_path", "/mcp"),
    )

def _error_response(
    message: str,
    *,
    error_code: str,
    latency_ms: int = 0,
) -> Dict[str, Any]:
    return {
        "status": "error",
        "error_code": error_code,
        "message": message,
        "latency_ms": latency_ms,
        "row_count": 0,
        "data": [],
    }

def _success_response(
    data: list[Dict[str, Any]],
    *,
    latency_ms: int,
) -> Dict[str, Any]:
    return {
        "status": "success",
        "error_code": "",
        "message": "ok",
        "latency_ms": latency_ms,
        "row_count": len(data),
        "data": data,
    }

# 2. 具备防御性编程的 MySQL 查询函数
@mcp.tool()
def query_mysql_database(sql_query: str) -> Dict[str, Any]:
    """
    Skill 执行体：安全地查询远程 MySQL 数据库
    """
    forbidden_keywords = ["insert", "update", "delete", "drop", "alter", "create", "replace", "truncate", "grant"]
    clean_query = sql_query.strip().lower()

    # 定义允许的合法只读前缀元组
    allowed_prefixes = ("select", "describe", "desc", "show", "explain")

    if not clean_query.startswith(allowed_prefixes):
        return {
            "status": "error",
            "message": "安全防御限制：本接口仅支持以 SELECT, DESCRIBE, SHOW, EXPLAIN 开头的只读语句。"
        }

    for kw in forbidden_keywords:
        if kw in clean_query:
            return {"status": "error", "message": f"安全防御限制：查询语句中包含禁止的敏感关键字 [{kw}]。"}

    conn = None
    try:
        conn = pymysql.connect(
            host=getattr(settings, "mysql_host"),
            port=int(getattr(settings, "mysql_port")),
            user=getattr(settings, "mysql_user"),            # 记得在 settings 里添加或这里改写
            password=getattr(settings, "mysql_password"),# 记得在 settings 里添加或这里改写
            database=getattr(settings, "mysql_db"),
            cursorclass=DictCursor,  # 自动返回字典格式
            connect_timeout=5        # 超时机制防止死锁
        )

        cursor = conn.cursor()
        cursor.execute(sql_query)
        rows = cursor.fetchall()
        data = list(rows) if rows else []  # 确保返回的是列表格式

        result= _success_response(data, latency_ms=0)
        result["audit"] = {
            "executed_query": sql_query,
            "row_count": len(data)
        }
        return result

    except pymysql.MySQLError as e:
        return _error_response(message=f"MySQL 数据库执行异常: {str(e)}", error_code="MYSQL_EXECUTION_ERROR")
    except Exception as e:
        return _error_response(message=f"执行查询时发生未知错误: {str(e)}", error_code="UNKNOWN_ERROR")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    mcp.run(transport="streamable-http")


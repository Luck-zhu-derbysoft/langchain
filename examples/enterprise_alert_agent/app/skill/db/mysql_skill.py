# app/skill/mysql_skills.py
from typing import Dict, Any
import pymysql
from pymysql.cursors import DictCursor
from app.config.settings import settings

# 1. 声明给大模型的 MySQL Tool 元数据
DB_TOOLS_METADATA = [
    {
        "type": "function",
        "function": {
            "name": "query_mysql_database",
            "description": (
                "执行只读 SQL 语句查询远程/公司中心的 MySQL 数据库（库名：dmatch），用于获取核心业务资产配置、生产环境数据等。"
                "注意：本工具仅支持 SELECT 查询。"
                "【提示】：如果想查询该 MySQL 数据库中所有的表名和对应的行数，可以直接使用 MySQL 系统表查询："
                "'SELECT table_name, table_rows AS row_count FROM information_schema.tables WHERE table_schema=\"dmatch\";'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql_query": {
                        "type": "string",
                        "description": "标准的 MySQL 只读 SQL 语句。例如: 'SELECT * FROM some_mysql_table LIMIT 5;'"
                    }
                },
                "required": ["sql_query"]
            }
        }
    }
]

# 2. 具备防御性编程的 MySQL 查询函数
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
        results = cursor.fetchall()
        cursor.close()

        return {"status": "success", "count": len(results), "data": results}

    except pymysql.MySQLError as e:
        return {"status": "error", "message": f"MySQL 数据库执行异常: {str(e)}"}
    except Exception as e:
        return {"status": "error", "message": f"执行查询时发生未知错误: {str(e)}"}
    finally:
        if conn:
            conn.close()

# 技能路由字典映射
DB_SKILL_MAP = {
    "query_mysql_database": query_mysql_database
}

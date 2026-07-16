
import logging
from typing import Any, Dict
from datetime import datetime
logger = logging.getLogger(__name__)

class DynamicSettings:
    def __init__(self)-> None:
        self._overrides: Dict[str, Any] = {}
        self._change_history: list = []

    def get(self, key: str, default: Any = None) -> Any:
        """获取动态配置项"""
        return self._overrides.get(key, default)

    def set(self, key: str, value: Any, user_id: str="system") -> bool:
        """设置动态配置项"""
        old_value = self._overrides.get(key)
        self._overrides[key] = value
        self._change_history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "key": key,
            "old_value": old_value,
            "new_value": value,
            "user_id": user_id,
        })
        return True
    def reset(self, key: str, user_id: str="system") -> bool:
        """重置动态配置项"""
        if key in self._overrides:
            del self._overrides[key]
            return True
        return False
    def get_all_overrides(self) -> Dict[str, Any]:
        """获取所有动态配置项"""
        return dict(self._overrides)
    def get_change_history(self, limit: int = 100) -> list:
        return sorted(self._change_history, key=lambda x: x["timestamp"], reverse=True)[:limit]
_dynamic_settings = DynamicSettings()

class ConfigManager:

    @staticmethod
    def set_task_max_retries(value: int, user_id: str = "system") -> bool:
        """设置任务最大重试次数"""
        return _dynamic_settings.set("task_max_retries", value)
    @staticmethod
    def get_task_timeout() -> float:
        """获取任务超时"""
        return _dynamic_settings.get("task_timeout_seconds", 20.0)

    @staticmethod
    def set_agent_max_iterations(count: int, user_id: str = "system") -> bool:
        """设置智能体最大迭代次数"""
        return _dynamic_settings.set("agent_max_iterations", count, user_id)

    @staticmethod
    def get_agent_max_iterations() -> int:
        """获取智能体最大迭代次数"""
        return _dynamic_settings.get("agent_max_iterations", 3)

    @staticmethod
    def set_enable_tool(tool_name: str, enabled: bool, user_id: str = "system") -> bool:
        """启用/禁用工具"""
        key = f"tool_enabled_{tool_name}"
        return _dynamic_settings.set(key, enabled, user_id)

    @staticmethod
    def is_tool_enabled(tool_name: str) -> bool:
        """检查工具是否启用"""
        key = f"tool_enabled_{tool_name}"
        return _dynamic_settings.get(key, True)

    @staticmethod
    def set_task_max_workers(workers: int, user_id: str = "system") -> bool:
        """设置最大并行数"""
        return _dynamic_settings.set("task_max_workers", workers, user_id)

    @staticmethod
    def get_task_max_workers() -> int:
        """获取最大并行数"""
        return _dynamic_settings.get("task_max_workers", 4)

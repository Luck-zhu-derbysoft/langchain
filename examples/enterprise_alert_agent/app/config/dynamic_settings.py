import logging
import threading
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)

# 动态配置项
ALLOWED_CONFIG_KEYS: dict[str, type] = {
    "agent_max_iterations": int,
    "task_max_retries": int,
    "task_timeout_seconds": float,
    "task_max_workers": int,
    "agent_tool_failure_threshold": int,
    "enable_fallback_chain": bool,
    "retrieval_final_k": int,
    "context_top_k": int,
    "memory_summary_update_turn_threshold": int,
}


class DynamicSettings:
    def __init__(self) -> None:
        self._overrides: dict[str, Any] = {}
        self._change_history: list = []
        self._lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        """获取动态配置项"""
        with self._lock:
            return self._overrides.get(key, default)

    def set(self, key: str, value: Any, user_id: str = "system") -> bool:
        """设置动态配置项"""
        if key not in ALLOWED_CONFIG_KEYS:
            logger.warning(f"Invalid dynamic config key: {key}")
            return False
        expectesd_type = ALLOWED_CONFIG_KEYS[key]
        try:
            value = expectesd_type(value)
        except Exception:
            logger.warning(f"Invalid value for dynamic config key {key}: {value}")
            return False
        with self._lock:
            self._overrides[key] = value
            self._change_history.append(
                {
                    "timestamp": datetime.utcnow().isoformat(),
                    "key": key,
                    "user_id": user_id,
                }
            )
            return True

    def reset(self, key: str, user_id: str = "system") -> bool:
        """重置动态配置项"""
        with self._lock:
            if key in self._overrides:
                del self._overrides[key]
                return True
        return False

    def get_all_overrides(self) -> dict[str, Any]:
        """获取所有动态配置项"""
        with self._lock:
            return dict(self._overrides)

    def get_change_history(self, limit: int = 100) -> list:
        with self._lock:
            history = sorted(
                self._change_history,
                key=lambda entry: entry["timestamp"],
                reverse=True,
            )[:limit]
            return deepcopy(history)


_dynamic_settings = DynamicSettings()


class ConfigManager:
    @staticmethod
    def set_task_max_retries(value: int, user_id: str = "system") -> bool:
        """设置任务最大重试次数"""
        return _dynamic_settings.set("task_max_retries", value, user_id)

    @staticmethod
    def get_task_max_retries() -> int:
        """获取任务最大重试次数"""
        return _dynamic_settings.get("task_max_retries", settings.task_max_retries)

    @staticmethod
    def get_task_timeout() -> float:
        """获取任务超时"""
        return _dynamic_settings.get("task_timeout_seconds", settings.task_timeout_seconds)

    @staticmethod
    def set_agent_max_iterations(count: int, user_id: str = "system") -> bool:
        """设置智能体最大迭代次数"""
        return _dynamic_settings.set("agent_max_iterations", count, user_id)

    @staticmethod
    def get_agent_max_iterations() -> int:
        """获取智能体最大迭代次数"""
        return _dynamic_settings.get("agent_max_iterations", settings.agent_max_iterations)

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
    def get_tool_failure_threshold() -> int:
        """获取工具连续失败阈值"""
        return _dynamic_settings.get(
            "agent_tool_failure_threshold", settings.agent_tool_failure_threshold
        )

    @staticmethod
    def set_task_max_workers(workers: int, user_id: str = "system") -> bool:
        """设置最大并行数"""
        return _dynamic_settings.set("task_max_workers", workers, user_id)

    @staticmethod
    def get_enable_fallback_chain() -> bool:
        """获取是否启用回退链"""
        return _dynamic_settings.get(
            "enable_fallback_chain",
            True,
        )

    @staticmethod
    def get_task_max_workers() -> int:
        """获取最大并行数"""
        return _dynamic_settings.get("task_max_workers", settings.task_max_workers)

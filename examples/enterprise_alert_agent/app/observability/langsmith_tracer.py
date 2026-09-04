"""LangSmith 显式追踪统一封装。

用于替代 @traceable 装饰器，提供显式的 run 生命周期管理。
"""

from __future__ import annotations

from typing import Any

from langsmith import Client
from langsmith.client import RUN_TYPE_T
from langsmith.run_trees import RunTree


class LangSmithTracer:
    #统一追踪客户端封装，提供显式的 root/child run 创建与结束
    def __init__(
            self,
            *,
            client: Client | None ,
            _enabled: bool ,
            project_name: str,
            service_name: str = "enterprise_alert_agent",
            ) -> None:
        self.client = client
        self._enabled = _enabled
        self.project_name = project_name
        self.service_name = service_name
    @property
    def enabled(self) -> bool:
        # 方便在业务代码中检查是否启用追踪
        return self._enabled and self.client is not None
    def start_root(
            self,
            *,
            name: str,
            inputs: dict[str, Any] | None = None,
            run_type: RUN_TYPE_T ,
            tags: list[str] | None = None,
            metadata: dict[str, Any] | None = None,
              ) -> RunTree | None:
        """创建并发送根 run。

        Args:
            name: run 名称（如 "api.chat"）
            run_type: run 类型（如 "chain", "llm", "retriever"）
            inputs: 输入参数字典
            tags: 标签列表
            metadata: 元数据字典

        Returns:
            RunTree 实例或 None（如果追踪未启用）
        """
        if not self.enabled:
            return None
        run = RunTree(
            name=name,
            run_type=run_type,
            inputs=inputs or {},
            tags=tags or [],
            extra={"metadata": {"service": self.service_name, **(metadata or {})}},
            project_name=self.project_name,
            ls_client=self.client,
        )
        run.post()
        return run# 立即发送至 LangSmith

    def start_child(
            self,
            *,
            parent_run: RunTree | None ,
            name: str,
            inputs: dict[str, Any] | None = None,
            run_type: RUN_TYPE_T ,
            tags: list[str] | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> RunTree | None:
            """在 parent run 下创建子 run。

        Args:
            parent_run: 父 run（如为 None 则返回 None）
            name: run 名称（如 "service.chat.ask"）
            run_type: run 类型
            inputs: 输入参数字典
            tags: 标签列表
            metadata: 元数据字典

        Returns:
            RunTree 实例或 None（如果追踪未启用或 parent_run 为 None）
        """
            if not self.enabled or parent_run is None:
                return None

            child = parent_run.create_child(
                name=name,
                run_type=run_type,
                inputs=inputs or {},
                tags=tags or [],
                extra={"metadata": {"service": self.service_name, **(metadata or {})}},
            )
            child.post()# 立即发送至 LangSmith
            return child

    def end_run(
            self,
            run: RunTree | None,
            *,
            outputs: dict[str, Any] | None = None,
            error :str |None = None,
            metadata: dict[str, Any] | None = None,
              ) -> None:
        """结束 run 并发送结果。

        Args:
            run: 要结束的 run（如为 None 则不执行任何操作）
            outputs: 输出参数字典
            error: 运行过程中捕获的异常（如有）
            metadata: 额外的元数据字典（如有）
        """
        if run is None:
            return
        #避免重复结束
        if run.end_time is not None:
            return
        run.end(outputs=outputs,error=error,metadata=metadata)
        run.patch()# 先发送结束状态，确保时间戳正确

    @staticmethod
    def format_error(exc: Exception) -> str:
        #辅助方法：将异常格式化为字符串，便于发送至 LangSmith
        return f"{exc.__class__.__name__}: {exc}"

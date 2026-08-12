"""人工干预处理引擎"""

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from app.infrastructure.agent.a2a_protocol import (
    ManualInterventionRequest,
    ManualInterventionResult,
)

logger = logging.getLogger(__name__)


class InterventionHandler:
    """人工干预处理引擎"""

    def __init__(self) -> None:
        self.intervention_queue: dict[str, list] = {}
        # 添加干预历史存储
        self.intervention_history: dict[str, list] = {}
        self.pending_interventions: dict[str, list] = {}
        self.intervention_results: dict[str, ManualInterventionResult] = {}

    def create_intervention_request(
        self, task_id: str, reason: str, user_id: str = "system"
    ) -> ManualInterventionRequest:
        """创建人工干预请求"""
        request = ManualInterventionRequest(
            task_id=task_id, intervention_type="pending", user_id=user_id
        )
        if request.task_id not in self.pending_interventions:
            self.pending_interventions[request.task_id] = []
        self.pending_interventions[request.task_id].append(request)
        logger.info(f"Created intervention request for task {task_id} with reason: {reason}")
        return request
    def submit_intervention(

        self,
        task_id: str,
        request: ManualInterventionRequest,
        execute_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> ManualInterventionResult:
        intervention_id = f"intervention_{task_id}_{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        """
        处理提交的干预请求

        Args:
            task_id:
            intervention: 干预请求任务ID
            execute_callback: 执行回调函数（用于重试或修改参数重试）
            timeout_seconds: 用户响应超时时间
        """

        try:
            # 处理不同类型的干预
            if request.intervention_type == "retry":
                logger.info(f"Retrying task {task_id} with intervention {intervention_id}")
                result = self.execute_intervention(request, execute_callback=execute_callback)
                self.intervention_results[intervention_id] = ManualInterventionResult(
                    intervention_id=intervention_id,
                    task_id=task_id,
                    success=True,
                    output=result,
                    elapsed_time_ms=(time.perf_counter() - started) * 1000,
                )
                return ManualInterventionResult(
                    intervention_id=intervention_id,
                    task_id=task_id,
                    success=True,
                    output=result,
                    elapsed_time_ms=(time.perf_counter() - started) * 1000,
                )
            elif request.intervention_type == "skip":
                logger.info(f"Skipping task {task_id} with intervention {intervention_id}")
                return ManualInterventionResult(
                    intervention_id=intervention_id,
                    task_id=task_id,
                    success=False,
                    output=f"Task {task_id} skipped. Reason: {request.skip_reason}",
                    elapsed_time_ms=(time.perf_counter() - started) * 1000,
                )
            elif request.intervention_type == "modify_params":
                logger.info(
                    f"Modifying parameters for task {task_id} with intervention {intervention_id}"
                )
                result = self.execute_intervention(request, execute_callback=execute_callback)
                self.intervention_results[intervention_id] = ManualInterventionResult(
                    intervention_id=intervention_id,
                    task_id=task_id,
                    success=True,
                    output=result,
                    elapsed_time_ms=(time.perf_counter() - started) * 1000,
                )
                return ManualInterventionResult(
                    intervention_id=intervention_id,
                    task_id=task_id,
                    success=True,
                    output=result,
                    elapsed_time_ms=(time.perf_counter() - started) * 1000,
                )
            elif request.intervention_type == "abort":
                logger.info(f"Aborting task {task_id} with intervention {intervention_id}")
                return ManualInterventionResult(
                    intervention_id=intervention_id,
                    task_id=task_id,
                    success=False,
                    output=f"任务 {task_id} 被用户干预中止。",
                    elapsed_time_ms=(time.perf_counter() - started) * 1000,
                )
            else:
                logger.error(
                    f"Unknown intervention type {request.intervention_type} for task {task_id}"
                )
                return ManualInterventionResult(
                    intervention_id=intervention_id,
                    task_id=task_id,
                    success=False,
                    output=f"未知的干预类型: {request.intervention_type}",
                    elapsed_time_ms=(time.perf_counter() - started) * 1000,
                )

        except Exception as e:
            logger.exception(f"Error processing intervention for task {task_id}: {e}")
            return ManualInterventionResult(
                intervention_id=intervention_id,
                task_id=task_id,
                success=False,
                output=f"处理干预请求时出错: {e!s}",
                elapsed_time_ms=(time.perf_counter() - started) * 1000,
            )
        finally:
            if request.task_id not in self.intervention_history:
                self.intervention_history[request.task_id] = []
            self.intervention_history[request.task_id].append(request)
            # 如果字典里存在 request.task_id：移除该 key，并返回对应 value
            self.pending_interventions.pop(request.task_id, None)
            if intervention_id not in self.intervention_results:
                self.intervention_results[intervention_id] = ManualInterventionResult(
                    intervention_id=intervention_id,
                    task_id=task_id,
                    success=False,
                    output="",
                    elapsed_time_ms=(time.perf_counter() - started) * 1000,
                )

    def get_pending_intervention(self, request_id: str) -> list:
        """获取待处理的干预请求"""
        return self.pending_interventions.get(request_id, [])

    def get_intervention_history(self, request_id: str) -> list:
        """获取干预历史"""
        return self.intervention_history.get(request_id, [])

    def add_pending_intervention(self, request_id: str, request: ManualInterventionRequest):
        """添加待处理的干预请求"""
        if request_id not in self.pending_interventions:
            self.pending_interventions[request_id] = []
        self.pending_interventions[request_id].append(request)

    def remove_pending_intervention(self, request_id: str, request: ManualInterventionRequest):
        """移除待处理的干预请求"""
        if request_id in self.pending_interventions:
            self.pending_interventions[request_id] = [
                iv for iv in self.pending_interventions[request_id] if iv != request
            ]

    def execute_intervention(
        self,
        request: ManualInterventionRequest,
        execute_callback: Callable[[dict[str, Any]], str] | None = None,
    ) -> str:
        """执行干预请求"""
        try:
            # 处理不同类型的干预
            if request.intervention_type == "retry":
                logger.info(
                    f"Retrying task {request.task_id} with intervention {request.intervention_type}"
                )
                if execute_callback:
                    return execute_callback(request.retry_params or {})
                return f"Task {request.task_id} retry submitted (no callback registered)"
            elif request.intervention_type == "skip":
                logger.info(
                    f"Skipping task {request.task_id} with intervention {request.intervention_type}"
                )
                return f"Task {request.task_id} skipped (no callback registered)"

            elif request.intervention_type == "modify_params":
                logger.info(
                    f"Modifying parameters for task {request.task_id} with intervention {request.intervention_type}"
                )
                if execute_callback:
                    return execute_callback(request.retry_params or {})
                return f"Task {request.task_id} modified (no callback registered)"
            elif request.intervention_type == "abort":
                logger.info(
                    f"Aborting task {request.task_id} with intervention {request.intervention_type}"
                )
                return f"Task {request.task_id} aborted (no callback registered)"
            else:
                logger.error(
                    f"Unknown intervention type {request.intervention_type} for task {request.task_id}"
                )
        except Exception as e:
            logger.exception(f"Error executing intervention for task {request.task_id}: {e}")
        return f"Executed intervention {request.intervention_type} for task {request.task_id}"

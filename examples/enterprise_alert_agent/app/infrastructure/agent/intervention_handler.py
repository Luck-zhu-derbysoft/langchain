"""人工干预处理引擎"""
import logging
import time
from typing import Callable, Dict
import uuid

from httpcore import request

from app.infrastructure.agent.a2a_protocol import ManualInterventionRequest, ManualInterventionResult

logger = logging.getLogger(__name__)

class InterventionHandler:
    """人工干预处理引擎"""
    def __init__(self)-> None:
        self.pending_interventions:Dict[str,ManualInterventionRequest] = {}
        self.intervention_results:Dict[str,ManualInterventionResult] = {}

    def create_intervention_request(self,tasK_id:str,reason:str,user_id:str="system")->ManualInterventionRequest:
        """创建人工干预请求"""
        request = ManualInterventionRequest(
            task_id=tasK_id,
            intervention_type="pending",
            user_id=user_id
        )
        self.pending_interventions[request.task_id] = request
        logger.info(f"Created intervention request for task {tasK_id} with reason: {reason}")
        return request

    def submit_intervention(self,task_id: str,
                                    intervention: ManualInterventionRequest,
                                    execute_callback: Callable[[ManualInterventionRequest], str],timeout_seconds: float = 300.0) -> ManualInterventionResult:
        intervention_id = f"intervention_{task_id}_{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        """
        处理提交的干预请求

        Args:
            task_id: 任务ID
            intervention: 干预请求
            execute_callback: 执行回调函数（用于重试或修改参数重试）
            timeout_seconds: 用户响应超时时间
        """

        try:
            # 处理不同类型的干预
            if intervention.intervention_type == "retry":
                logger.info(f"Retrying task {task_id} with intervention {intervention_id}")
                result = execute_callback(intervention)
                return ManualInterventionResult(
                     intervention_id=intervention_id,
                    task_id=task_id,
                    success=True,
                    output=result,
                    elapsed_time_ms=(time.perf_counter() - started) * 1000
                )
            elif intervention.intervention_type == "skip":
                logger.info(f"Skipping task {task_id} with intervention {intervention_id}")
                return ManualInterventionResult(
                    intervention_id=intervention_id,
                    task_id=task_id,
                    success=False,
                    output=f"Task {task_id} skipped. Reason: {intervention.skip_reason}",
                    elapsed_time_ms=(time.perf_counter() - started) * 1000
                )
            elif intervention.intervention_type == "modify_params":
                logger.info(f"Modifying parameters for task {task_id} with intervention {intervention_id}")
                result = execute_callback(intervention)
                return ManualInterventionResult(
                    intervention_id=intervention_id,
                    task_id=task_id,
                    success=True,
                    output=result,
                    elapsed_time_ms=(time.perf_counter() - started) * 1000
                )
            elif intervention.intervention_type == "abort":
                logger.info(f"Aborting task {task_id} with intervention {intervention_id}")
                return ManualInterventionResult(
                    intervention_id=intervention_id,
                    task_id=task_id,
                    success=False,
                    output=f"任务 {task_id} 被用户干预中止。",
                    elapsed_time_ms=(time.perf_counter() - started) * 1000
                )
            else:
                logger.error(f"Unknown intervention type {intervention.intervention_type} for task {task_id}")
                return ManualInterventionResult(
                    intervention_id=intervention_id,
                    task_id=task_id,
                    success=False,
                    output=f"未知的干预类型: {intervention.intervention_type}",
                    elapsed_time_ms=(time.perf_counter() - started) * 1000
                )
        except Exception as e:
            logger.exception(f"Error processing intervention for task {task_id}: {e}")
            return ManualInterventionResult(
                intervention_id=intervention_id,
                task_id=task_id,
                success=False,
                output=f"处理干预请求时出错: {str(e)}",
                elapsed_time_ms=(time.perf_counter() - started) * 1000
            )
        finally:
            self.pending_interventions.pop(task_id, None)
            self.intervention_results[intervention_id] = ManualInterventionResult(
                intervention_id=intervention_id,
                task_id=task_id,
                success=False,
                elapsed_time_ms=(time.perf_counter() - started) * 1000
            )



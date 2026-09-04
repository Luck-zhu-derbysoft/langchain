"""故障分析和诊断引擎"""

import logging

from app.infrastructure.fault.fault_types import (
    FaultContext,
    FaultDiagnosis,
    FaultSeverity,
    FaultType,
)

logger = logging.getLogger(__name__)


class FaultAnalyzer:
    """故障分析器 - 根据错误信息生成诊断和恢复建议"""

    def analyze(self, context: FaultContext) -> FaultDiagnosis:
        """
        分析故障并生成诊断报告

        Args:
            context: 故障上下文信息

        Returns:
            FaultDiagnosis: 诊断结果
        """
        fault_type = self._classify_fault(context)
        severity = self._assess_severity(fault_type, context)
        root_cause = self._analyze_root_cause(fault_type, context)
        suggestions = self._generate_suggestions(fault_type, context)
        retry_feasible = self._can_retry(fault_type, context)
        recovery_time = self._estimate_recovery_time(fault_type)
        retry_recommendation = self._recommend_retry(fault_type, context)

        diagnosis = FaultDiagnosis(
            fault_id=f"fault_{context.request_id}_{context.task_id}",
            fault_type=fault_type,
            severity=severity,
            root_cause=root_cause,
            affected_tasks=[context.task_id],
            recovery_suggestions=suggestions,
            retry_feasible=retry_feasible,
            estimated_recovery_time=recovery_time,
            retry_recommendation=retry_recommendation,
            context={
                "agent_id": context.agent_id,
                "tool_name": context.tool_name,
                "retry_count": context.retry_count,
                "elapsed_time_ms": context.elapsed_time_ms,
            },
        )

        logger.warning(
            "[%s] Fault diagnosed: type=%s, severity=%s, root_cause=%s",
            context.request_id,
            fault_type.value,
            severity.value,
            root_cause[:50],
        )

        return diagnosis

    @staticmethod
    def _classify_fault(context: FaultContext) -> FaultType:
        """根据错误消息分类故障类型"""
        error = context.error_message.lower()

        # 工具相关错误
        if "not found" in error or "unknown tool" in error:
            return FaultType.TOOL_NOT_FOUND
        if "timeout" in error or "timed out" in error:
            return FaultType.TOOL_TIMEOUT
        if "circuit" in error or "熔断" in error:
            return FaultType.AGENT_CIRCUIT_OPEN
        if "401" in error or "unauthorized" in error or "auth" in error:
            return FaultType.AUTH_ERROR
        if "429" in error or "rate limit" in error:
            return FaultType.RATE_LIMIT
        if "connection" in error or "network" in error or "socket" in error:
            return FaultType.NETWORK_ERROR
        if "dependency" in error or "depends on" in error:
            return FaultType.DEPENDENCY_FAILED

        # 默认为执行错误
        return FaultType.TOOL_EXECUTION_ERROR

    @staticmethod
    def _assess_severity(fault_type: FaultType, context: FaultContext) -> FaultSeverity:
        """评估故障严重程度"""
        # 已重试多次
        if context.retry_count >= 2:
            return FaultSeverity.CRITICAL

        # 严重故障类型
        if fault_type in [FaultType.AUTH_ERROR, FaultType.AGENT_CIRCUIT_OPEN]:
            return FaultSeverity.HIGH

        # 中等故障类型
        if fault_type in [FaultType.TOOL_TIMEOUT, FaultType.NETWORK_ERROR, FaultType.RATE_LIMIT]:
            return FaultSeverity.MEDIUM

        # 低等故障
        if fault_type in [FaultType.TOOL_NOT_FOUND, FaultType.DEPENDENCY_FAILED]:
            return FaultSeverity.LOW

        return FaultSeverity.MEDIUM

    @staticmethod
    def _analyze_root_cause(fault_type: FaultType, context: FaultContext) -> str:
        """分析根因"""
        causes = {
            FaultType.TOOL_NOT_FOUND: f"工具 '{context.tool_name}' 不存在或未注册。请检查工具配置。",
            FaultType.TOOL_EXECUTION_ERROR: f"工具 '{context.tool_name}' 执行失败: {context.error_message[:100]}",
            FaultType.TOOL_TIMEOUT: f"工具 '{context.tool_name}' 在规定时间内未响应（已耗时 {context.elapsed_time_ms:.0f}ms）。",
            FaultType.AGENT_CIRCUIT_OPEN: f"智能体 '{context.agent_id}' 熔断打开，连续失败已达阈值。",
            FaultType.AUTH_ERROR: "认证失败：API 密钥无效或已过期。",
            FaultType.NETWORK_ERROR: "网络连接失败：无法连接到外部服务。",
            FaultType.RATE_LIMIT: "API 请求频率超限：请稍后重试。",
            FaultType.DEPENDENCY_FAILED: f"依赖任务失败，无法继续执行 '{context.task_id}'。",
            FaultType.UNKNOWN: f"未知错误: {context.error_message[:100]}",
        }
        return causes.get(fault_type, "系统内部错误")

    @staticmethod
    def _generate_suggestions(fault_type: FaultType, context: FaultContext) -> list[str]:
        """生成恢复建议"""
        suggestions = []

        if fault_type == FaultType.TOOL_NOT_FOUND:
            suggestions = [
                f"检查工具 '{context.tool_name}' 是否已正确注册",
                "查看 settings.py 中的工具配置",
                "尝试使用备选工具（如果可用）",
            ]
        elif fault_type == FaultType.TOOL_TIMEOUT:
            suggestions = [
                f"增加超时时间（当前: {context.elapsed_time_ms:.0f}ms）",
                "检查外部服务是否正常响应",
                "尝试使用备选工具或备选智能体",
            ]
        elif fault_type == FaultType.AUTH_ERROR:
            suggestions = [
                "检查 API 密钥是否正确设置在环境变量中",
                "确保 API 密钥未过期",
                "尝试重新生成 API 密钥",
            ]
        elif fault_type == FaultType.RATE_LIMIT:
            suggestions = [
                "等待 30-60 秒后重试",
                "减少并发请求数",
                "升级 API 额度或联系服务提供商",
            ]
        elif fault_type == FaultType.NETWORK_ERROR:
            suggestions = [
                "检查网络连接状态",
                "检查防火墙/代理设置",
                "尝试使用备选网络路径或 VPN",
            ]
        elif fault_type == FaultType.AGENT_CIRCUIT_OPEN:
            suggestions = [
                "等待熔断恢复（30秒后自动尝试）",
                f"检查智能体 '{context.agent_id}' 的日志",
                "考虑手动重置熔断状态",
            ]
        else:
            suggestions = [
                "重试操作（最多 3 次）",
                "查看详细错误日志",
                "如问题持续，请联系技术支持",
            ]

        return suggestions

    @staticmethod
    def _can_retry(fault_type: FaultType, context: FaultContext) -> bool:
        """判断是否可以重试"""
        # 已达重试上限
        if context.retry_count >= 3:
            return False

        # 不应该重试的故障类型
        no_retry_faults = [
            FaultType.TOOL_NOT_FOUND,
            FaultType.AUTH_ERROR,
            FaultType.AGENT_CIRCUIT_OPEN,
        ]

        if fault_type in no_retry_faults:
            return False

        return True

    @staticmethod
    def _estimate_recovery_time(fault_type: FaultType) -> float:
        """估算恢复时间（秒）"""
        recovery_times = {
            FaultType.TOOL_TIMEOUT: 30.0,  # 超时通常需要等待
            FaultType.RATE_LIMIT: 60.0,  # 限流需要等待更长
            FaultType.AGENT_CIRCUIT_OPEN: 30.0,  # 熔断自动恢复时间
            FaultType.NETWORK_ERROR: 10.0,  # 网络错误可能快速恢复
            FaultType.TOOL_EXECUTION_ERROR: 5.0,  # 执行错误可快速重试
        }
        return recovery_times.get(fault_type, 5.0)

    @staticmethod
    def _recommend_retry(fault_type: FaultType, context: FaultContext) -> str:
        """推荐重试策略"""
        if context.retry_count >= 3:
            return "no_retry"  # 不要重试

        if fault_type in [FaultType.RATE_LIMIT, FaultType.AGENT_CIRCUIT_OPEN]:
            return "wait_30s"  # 等待 30 秒后重试

        if fault_type == FaultType.TOOL_TIMEOUT:
            return "wait_10s"  # 等待 10 秒后重试

        if fault_type in [FaultType.TOOL_EXECUTION_ERROR, FaultType.NETWORK_ERROR]:
            return "immediate"  # 立即重试

        return "skip"  # 跳过此任务

"""LangGraph 故障恢复编排 —— A0 试点（L1→L4 降级 + 人工干预）。

知识点参考文档：
snapshot.next 详解（LangGraph 故障恢复工作流）
"""

from __future__ import annotations

import logging
from collections.abc import Hashable
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from app.infrastructure.fault.fault_analyzer import FaultAnalyzer
from app.infrastructure.fault.fault_types import (
    FaultContext,
    FaultDiagnosis,
    FaultSeverity,
    FaultType,
)

logger = logging.getLogger(__name__)

DEFAULT_RECURSION_LIMIT = 60
HUMAN_RETRY, HUMAN_SKIP, HUMAN_ABORT = "retry", "skip", "abort"


# 1) 类型化 AgentState + reducer
def _append_trail(cur: list[str], upd: list[str] | str) -> list[str]:
    """trail 通道的 reducer：把节点名追加进轨迹（str 或 list[str] 都行）。

    轨迹 = 可视化材料 + 审计记录 + 断点恢复时判断"已走到哪一档"的依据。
    """
    if isinstance(upd, str):
        return [*cur, upd]
    else:
        return [*cur, *upd]


class FaultRecoveryState(TypedDict, total=False):
    """类型化状态：收敛降级链路散落的中间变量。

    除 trail 用 append reducer 外，其余都是"最后写入生效"（本链路串行执行，
    同一时刻只有一个节点在写）。全字段可被 Checkpointer 持久化。
    """

    request_id: str
    task_id: str
    fault_context: dict[str, Any]  # FaultContext 的 dict 化（可序列化）
    diagnosis: dict[str, Any]  # FaultDiagnosis 的 dict 化（枚举转 value）
    level: int  # 1=L1 2=L2 3=L3 4=L4
    attempts: int  # L1 已重试次数
    human_rounds: int  # 已进入 L4 的次数
    trail: Annotated[list[str], _append_trail]
    resolved: bool
    output: str
    error_message: str
    human_decision: str  # HITL 恢复值：retry/skip/abort


# 2) 执行器契约（注入真实逻辑）
@dataclass
class RecoveryAttempt:
    """某一降级档位单次尝试的结果。"""

    success: bool
    output: str = ""


@dataclass
class FaultExecutor:
    """L1/L2/L3 的真实执行器，由调用方注入；某档位缺能力置 None 会自动跳档。

    retry:    L1 自动重试（带退避的再次执行）
    fallback: L2 调参降级（切备用模型/备用工具/调参后执行）
    rag_only: L3 仅 RAG（去掉工具链路，纯知识库兜底）
    """

    retry: Any = None
    fallback: Any = None
    rag_only: Any = None


@dataclass
class RecoveryOutcome:
    """某一降级档位尝试的最终结果。"""

    status: str  # "completed" | "awaiting_human"
    resolved: bool = False
    output: str = ""
    level: int = 0
    attempts: int = 0
    human_rounds: int = 0
    trail: list[str] = field(default_factory=list)
    diagnosis: FaultDiagnosis | None = None
    thread_id: str = ""
    human_question: dict[str, Any] | None = None  # 人工决策时的 interrupt 载荷


# 3) dataclass <-> dict（保证 Checkpoint 可序列化，生产可换 PostgresSaver）
def _ctx_to_dict(ctx: FaultContext) -> dict[str, Any]:
    """FaultContext -> dict."""
    return {
        "request_id": ctx.request_id,
        "task_id": ctx.task_id,
        "agent_id": ctx.agent_id,
        "tool_name": ctx.tool_name,
        "error_message": ctx.error_message,
        "error_type": ctx.error_type,
        "retry_count": ctx.retry_count,
        "elapsed_time_ms": ctx.elapsed_time_ms,
    }


def _ctx_from_dict(d: dict[str, Any]) -> FaultContext:
    """dict -> FaultContext."""
    return FaultContext(
        request_id=str(d["request_id"]),
        task_id=str(d["task_id"]),
        agent_id=str(d.get("agent_id", "")),
        tool_name=str(d.get("tool_name", "")),
        error_message=str(d.get("error_message", "")),
        error_type=str(d.get("error_type", "")),
        retry_count=int(d.get("retry_count", 0)),
        elapsed_time_ms=float(d.get("elapsed_time_ms", 0.0)),
    )


def _diag_to_dict(d: FaultDiagnosis) -> dict[str, Any]:
    """FaultDiagnosis -> dict."""
    return {
        "fault_id": d.fault_id,
        "fault_type": d.fault_type.value,
        "severity": d.severity.value,
        "root_cause": d.root_cause,
        "affected_tasks": list(d.affected_tasks),
        "recovery_suggestions": list(d.recovery_suggestions),
        "retry_feasible": bool(d.retry_feasible),
        "estimated_recovery_time": float(d.estimated_recovery_time),
        "retry_recommendation": d.retry_recommendation,
        "context": dict(d.context),
    }


def _diag_from_dict(d: dict[str, Any]) -> FaultDiagnosis:
    """dict -> FaultDiagnosis."""
    return FaultDiagnosis(
        fault_id=str(d["fault_id"]),
        fault_type=FaultType(str(d["fault_type"])),
        severity=FaultSeverity(str(d["severity"])),
        root_cause=str(d["root_cause"]),
        affected_tasks=list(d.get("affected_tasks", [])),
        recovery_suggestions=list(d.get("recovery_suggestions", [])),
        retry_feasible=bool(d.get("retry_feasible", False)),
        estimated_recovery_time=float(d.get("estimated_recovery_time", 0.0)),
        retry_recommendation=str(d.get("retry_recommendation", "")),
        context=dict(d.get("context", {})),
    )


def retry_backoff_seconds(diag: FaultDiagnosis) -> float:
    """L1 退避秒数映射：与 fault_analyzer._recommend_retry 对齐。"""
    return {"wait_30s": 30.0, "wait_10s": 10.0, "immediate": 1.0}.get(
        diag.retry_recommendation, 0.0
    )


# 4) 工作流：节点 + 条件边/循环边
class FaultRecoveryWorkflow:
    """StateGraph：一条由 _route 统一分发的降级阶梯。"""

    LEVEL_NODES: dict[int, str] = {  # noqa: RUF012
        1: "l1_retry",
        2: "l2_adjust",
        3: "l3_rag_only",
        4: "l4_human_intervention",
    }
    _ROUTE_TARGETS: dict[Hashable, str] = {  # noqa: RUF012
        "l1_retry": "l1_retry",
        "l2_adjust": "l2_adjust",
        "l3_rag_only": "l3_rag_only",
        "l4_human_intervention": "l4_human_intervention",
        "end": END,
    }

    def __init__(
        self, analyzer=None, executor=None, *, max_retry_attempts: int = 3, checkpointer=None
    ) -> None:
        self.__analyzer = analyzer or FaultAnalyzer()
        self.__executor = executor or FaultExecutor()
        self.max_retry_attempts = max_retry_attempts
        self.__checkpoint_saver = checkpointer
        self.__checkpointer = checkpointer
        self._compiled_graph: CompiledStateGraph | None = None

    async def _analyze_fault(self, state: FaultRecoveryState) -> dict[str, Any]:
        """故障分析：产出诊断，决定起始档位（可重试→L1；不可重试但有降级→L2…）。"""
        raw = state.get("fault_context")
        if not raw:
            raise ValueError("缺少 fault_context")
        ctx = _ctx_from_dict(raw)
        if state.get("diagnosis") is not None:
            diag = _diag_from_dict(state.get("diagnosis"))  # type: ignore
        else:
            diag = self.__analyzer.analyze(ctx)
        if diag.retry_feasible and self.__executor.retry is not None:
            level = 1
        elif self.__executor.fallback is not None:
            level = 2
        elif self.__executor.rag_only is not None:
            level = 3
        else:
            level = 4
        return {
            "diagnosis": _diag_to_dict(diag),
            "level": level,
            "human_rounds": 0,
            "resolved": False,
            "output": "",
            "error_message": diag.root_cause,
            "human_decision": "",
            "trail": "analyze_fault",
        }

    def _route(self, state: FaultRecoveryState) -> str:
        """条件边路由：已解决→END；否则按 level 分派到对应档位节点。

        同档重试 = 循环边（level 不变，route 又指回同一节点）；
        升级 = 条件边（level 递增，route 指向下一档）。
        """
        if state.get("resolved"):
            return "end"
        return self.LEVEL_NODES.get(int(state.get("level", 0)), "end")

    async def _l1_retry(self, state: FaultRecoveryState) -> dict[str, Any]:
        """L1 自动重试；达到 max_retry_attempts 升 L2。"""
        if self.__executor.retry is None:
            return {"level": 2, "trail": "l1_retry:skip"}
        ctx = _ctx_from_dict(state.get("fault_context"))  # type: ignore
        n = int(state.get("attempts", 0))
        attempt = await self.__executor.retry(ctx, n)
        if attempt.success:
            return {"resolved": True, "output": attempt.output, "trail": "l1_retry:ok"}
        n += 1
        if n >= self.max_retry_attempts:
            return {"level": 2, "attempts": n, "trail": "l1_retry:fail->L2"}
        return {
            "resolved": False,
            "attempts": n,
            "trail": "l1_retry:fail->retry",
        }

    async def _l2_adjust(self, state: FaultRecoveryState) -> dict[str, Any]:
        """L2 调参降级；失败后进入 L3。"""
        if self.__executor.fallback is None:
            return {"level": 3, "trail": "l2_adjust:skip"}
        ctx = _ctx_from_dict(state.get("fault_context"))  # type: ignore
        diag = _diag_from_dict(state.get("diagnosis"))  # type: ignore
        attempt = await self.__executor.fallback(ctx, diag)  # type: ignore
        if attempt.success:
            return {
                "resolved": True,
                "output": attempt.output,
                "trail": "l2_adjust:ok",
            }
        return {
            "level": 3,
            "error_message": attempt.output or diag.root_cause,
            "trail": "l2_adjust:fail->L3",
        }

    async def _l3_rag_only(self, state: FaultRecoveryState) -> dict[str, Any]:
        """L3 仅 RAG 兜底；失败后进入 L4。"""
        if self.__executor.rag_only is None:
            return {"level": 4, "trail": "l3_rag_only:skip"}
        ctx = _ctx_from_dict(state.get("fault_context"))  # type: ignore
        diag = _diag_from_dict(state.get("diagnosis"))  # type: ignore
        attempt = await self.__executor.rag_only(ctx, diag)  # type: ignore
        if attempt.success:
            return {
                "resolved": True,
                "output": attempt.output,
                "trail": "l3_rag_only:ok",
            }
        return {
            "level": 4,
            "error_message": attempt.output or diag.root_cause,
            "trail": "l3_rag_only:fail->L4",
        }

    async def _l4_human_intervention(self, state: FaultRecoveryState) -> dict[str, Any]:
        """L4 人工处理：执行 human_intervention。"""
        """L4 人工干预：LangGraph 原生 HITL（interrupt()）。

        节点在此暂停（ainvoke 正常返回、state.next 指向本节点）；人工用
        Command(resume=...) 恢复：retry→回 L1；skip/abort→结束。
        """
        diag_raw = state.get("diagnosis") or {}
        task_id = str(state.get("task_id", ""))
        payload: dict[str, Any] = {
            "type": "fault_recovery_hitl",
            "task_id": task_id,
            "fault_type": str(diag_raw.get("fault_type", "unknown")),
            "severity": str(diag_raw.get("severity", "unknown")),
            "root_cause": str(diag_raw.get("root_cause") or state.get("error_message", ""))[:300],
            "suggestions": list(diag_raw.get("recovery_suggestions", [])),
            "options": [HUMAN_RETRY, HUMAN_SKIP, HUMAN_ABORT],
            "human_rounds": int(state.get("human_rounds", 0)) + 1,
        }
        decision = (interrupt(payload) or HUMAN_SKIP).strip().lower()
        rounds = int(state.get("human_rounds", 0)) + 1
        base = {
            "human_decision": decision,
            "human_rounds": rounds,
        }
        if decision == HUMAN_RETRY:
            return {
                **base,
                "level": 1,
                "trail": "l4_human_intervention:retry",
            }
        if decision == HUMAN_ABORT:
            return {
                **base,
                "resolved": True,
                "output": f"任务 {task_id} 已由人工中止。",
                "trail": "l4_human_intervention:abort",
            }
        return {
            **base,
            "resolved": True,
            "output": f"任务 {task_id} 已由人工跳过，保留待处理。",
            "trail": "l4_human_intervention:skip",
        }

    def build(self) -> StateGraph:
        """START→analyze_fault；每个节点经 _route 条件边分发（含循环/升级边）。"""
        g = StateGraph(FaultRecoveryState)
        g.add_node("analyze_fault", self._analyze_fault)
        for name in self.LEVEL_NODES.values():
            g.add_node(name, getattr(self, f"_{name}"))
        g.add_edge(START, "analyze_fault")
        for src in ("analyze_fault", *self.LEVEL_NODES.values()):
            g.add_conditional_edges(src, self._route, self._ROUTE_TARGETS)
        return g

    def _compile(self) -> CompiledStateGraph:
        """编译成 StateGraph。"""
        if self._compiled_graph is None:
            self._compiled_graph = self.build().compile(
                # 这意味着服务重启后，L4 中断点会丢失。
                # agent_task_state 虽然在 PG 中，但没有 LangGraph snapshot，无法 resume。
                # 生产环境应改为由应用启动时创建的 AsyncPostgresSaver 注入：
                checkpointer=self.__checkpointer or MemorySaver()
            )
        return self._compiled_graph

    def diagram(self) -> str:
        """绘制图结构。"""
        return self._compile().get_graph().draw_mermaid()

    # ---------- 运行 ----------
    @staticmethod
    def _initial_state(ctx: FaultContext) -> dict[str, Any]:
        return {
            "request_id": ctx.request_id,
            "task_id": ctx.task_id,
            "fault_context": _ctx_to_dict(ctx),
            "level": 0,
            "attempts": 0,
            "human_rounds": 0,
            "trail": [],
            "resolved": False,
            "output": "",
            "error_message": "",
            "human_decision": "",
        }

    @staticmethod
    def _to_outcome(
        values: dict[str, Any],
        *,
        status: str,
        thread_id: str,
        human_question: dict[str, Any] | None = None,
    ) -> RecoveryOutcome:
        diag_raw = values.get("diagnosis")
        return RecoveryOutcome(
            status=status,
            resolved=bool(values.get("resolved", False)),
            output=str(values.get("output", "")),
            level=int(values.get("level", 0)),
            attempts=int(values.get("attempts", 0)),
            human_rounds=int(values.get("human_rounds", 0)),
            trail=list(values.get("trail", [])),
            diagnosis=_diag_from_dict(diag_raw) if diag_raw is not None else None,
            thread_id=thread_id,
            human_question=human_question,
        )

    async def run(
        self,
        ctx: FaultContext | None,
        *,
        thread_id: str = "default",
        resume: str | None = None,
        recursion_limit: int = DEFAULT_RECURSION_LIMIT,
    ) -> RecoveryOutcome:
        """执行/恢复一次故障恢复流程。

        resume=None 开始新流程；否则以人工决策恢复 L4 暂停点。
        返回 status=awaiting_human 表示停在 L4，等待 resume。
        """
        graph = self._compile()
        # 使用编译后的图实例，而不是每次都调用 self._compile()
        config: RunnableConfig = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": recursion_limit,
        }
        if resume is None:
            if ctx is None:
                raise ValueError("ctx must be provided when starting a new recovery process")
            await graph.ainvoke(self._initial_state(ctx), config)
        else:
            await graph.ainvoke(Command(resume=resume), config)
        snapshot = await graph.aget_state(config)
        human_question = None
        if snapshot.next:
            for task in snapshot.tasks:
                if task.interrupts:
                    human_question = task.interrupts[0].value
                    break
            if human_question is not None:
                return self._to_outcome(
                    snapshot.values,
                    status="awaiting_human",
                    thread_id=thread_id,
                    human_question=human_question,
                )
        return self._to_outcome(
            snapshot.values,
            status="completed",
            thread_id=thread_id,
        )

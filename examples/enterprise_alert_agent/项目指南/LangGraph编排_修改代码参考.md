# LangGraph 编排 —— 修改代码参考

> 对应 `LEARNING_GUIDE.md` 技能清单第 3 块「Agent 核心技能」的 **LangGraph 编排** 项：
>
> | 技能点 | 项目现状 | 落地练习 | 优先级 |
> |---|---|---|---|
> | LangGraph 编排 | ❌ 未图化 | L1→L4 `StateGraph` | 🟠 |
>
> 依据 `ENTERPRISE_EVALUATION-v2.md` 的 **A0 试点**：把 `fault_analyzer.py` 的
> **L1 重试 → L2 调参 → L3 仅 RAG → L4 人工** 决策链，用 `StateGraph` 的
> 「节点 + 条件边 + 循环边 + 原生 `interrupt()`(HITL)」表达，并复用现有
> `FaultAnalyzer` / `FaultContext` / `FaultDiagnosis`。

---

## 一、改动文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/infrastructure/agent/langgraph_fault_recovery.py` | 新增 | StateGraph 核心模块（L1→L4 + HITL） |
| `tests/unit_tests/test_langgraph_fault_recovery.py` | 新增（可选） | 单测：条件边 / 循环边 / HITL |
| `chat_service.py` | 不改动 | 试点先行，禁止大爆炸重写（见「四、后续接线」） |

> 关键取舍（`ENTERPRISE_EVALUATION-v2.md` 明确）：**只图化核心复杂链路，
> 简单链路（单 Agent 工具调用、纯 RAG 问答）保持现状**。

---

## 二、文件 1：`app/infrastructure/agent/langgraph_fault_recovery.py`

```python
"""LangGraph 故障恢复编排 —— A0 试点（L1→L4 降级 + 人工干预）。

用 StateGraph 表达原本散落在 chat_service 里的手写 retry/fallback 阶梯：
  L1 自动重试  → L2 调参降级 → L3 仅 RAG → L4 人工干预(HITL)

State 只存可 msgpack 序列化的普通 dict（dataclass 只活在节点局部），
因此可接 Checkpointer 做断点恢复；各档位"干活"的执行器由 FaultExecutor 注入，
单测/只读 demo 注入 fake 执行器即可，无需 LLM / DB / Redis。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Annotated, Any, Hashable, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from app.infrastructure.fault.fault_analyzer import FaultAnalyzer
from app.infrastructure.fault.fault_types import (
    FaultContext, FaultDiagnosis, FaultSeverity, FaultType,
)

logger = logging.getLogger(__name__)

DEFAULT_RECURSION_LIMIT = 60
HUMAN_RETRY, HUMAN_SKIP, HUMAN_ABORT = "retry", "skip", "abort"


# --------------------------------------------------------------------------
# 1) 类型化 AgentState + reducer
# --------------------------------------------------------------------------
def _append_trail(cur: list[str], upd: list[str] | str) -> list[str]:
    """trail 通道的 reducer：把节点名追加进轨迹（str 或 list[str] 都行）。

    轨迹 = 可视化材料 + 审计记录 + 断点恢复时判断"已走到哪一档"的依据。
    """
    if isinstance(upd, str):
        return [*cur, upd]
    return [*cur, *upd]


class FaultRecoveryState(TypedDict, total=False):
    """类型化状态：收敛降级链路散落的中间变量。

    除 trail 用 append reducer 外，其余都是"最后写入生效"（本链路串行执行，
    同一时刻只有一个节点在写）。全字段可被 Checkpointer 持久化。
    """
    request_id: str
    task_id: str
    fault_context: dict[str, Any]        # FaultContext 的 dict 化（可序列化）
    diagnosis: dict[str, Any]            # FaultDiagnosis 的 dict 化（枚举转 value）
    level: int                           # 1=L1 2=L2 3=L3 4=L4
    attempts: int                        # L1 已重试次数
    human_rounds: int                    # 已进入 L4 的次数
    trail: Annotated[list[str], _append_trail]
    resolved: bool
    output: str
    error_message: str
    human_decision: str                  # HITL 恢复值：retry/skip/abort


# --------------------------------------------------------------------------
# 2) 执行器契约（注入真实逻辑）
# --------------------------------------------------------------------------
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
    """一次（或一轮恢复后）的运行结果。"""
    status: str                     # "completed" | "awaiting_human"
    resolved: bool = False
    output: str = ""
    level: int = 0
    attempts: int = 0
    human_rounds: int = 0
    trail: list[str] = field(default_factory=list)
    diagnosis: FaultDiagnosis | None = None
    thread_id: str = ""
    human_question: dict[str, Any] | None = None   # 等人工决策时的 interrupt 载荷


# --------------------------------------------------------------------------
# 3) dataclass <-> dict（保证 Checkpoint 可序列化，生产可换 PostgresSaver）
# --------------------------------------------------------------------------
def _ctx_to_dict(ctx: FaultContext) -> dict[str, Any]:
    return {
        "request_id": ctx.request_id, "task_id": ctx.task_id,
        "agent_id": ctx.agent_id, "tool_name": ctx.tool_name,
        "error_message": ctx.error_message, "error_type": ctx.error_type,
        "retry_count": ctx.retry_count, "elapsed_time_ms": ctx.elapsed_time_ms,
    }


def _ctx_from_dict(d: dict[str, Any]) -> FaultContext:
    return FaultContext(
        request_id=str(d["request_id"]), task_id=str(d["task_id"]),
        agent_id=str(d.get("agent_id", "")), tool_name=str(d.get("tool_name", "")),
        error_message=str(d.get("error_message", "")), error_type=str(d.get("error_type", "")),
        retry_count=int(d.get("retry_count", 0)), elapsed_time_ms=float(d.get("elapsed_time_ms", 0.0)),
    )


def _diag_to_dict(d: FaultDiagnosis) -> dict[str, Any]:
    return {
        "fault_id": d.fault_id, "fault_type": d.fault_type.value,
        "severity": d.severity.value, "root_cause": d.root_cause,
        "affected_tasks": list(d.affected_tasks),
        "recovery_suggestions": list(d.recovery_suggestions),
        "retry_feasible": bool(d.retry_feasible),
        "estimated_recovery_time": float(d.estimated_recovery_time),
        "retry_recommendation": d.retry_recommendation, "context": dict(d.context),
    }


def _diag_from_dict(d: dict[str, Any]) -> FaultDiagnosis:
    return FaultDiagnosis(
        fault_id=str(d["fault_id"]), fault_type=FaultType(str(d["fault_type"])),
        severity=FaultSeverity(str(d["severity"])), root_cause=str(d["root_cause"]),
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
        diag.retry_recommendation, 0.0)


# --------------------------------------------------------------------------
# 4) 工作流：节点 + 条件边/循环边
# --------------------------------------------------------------------------
class FaultRecoveryWorkflow:
    """StateGraph：一条由 _route 统一分发的降级阶梯。"""

    LEVEL_NODES: dict[int, str] = {
        1: "l1_retry", 2: "l2_adjust", 3: "l3_rag_only", 4: "l4_human_intervention",
    }
    # path_map 的 key 必须是 Hashable，str 是 Hashable 但 dict 类型不变性会让
    # 类型检查报错，所以声明为 dict[Hashable, str]
    _ROUTE_TARGETS: dict[Hashable, str] = {
        "l1_retry": "l1_retry", "l2_adjust": "l2_adjust",
        "l3_rag_only": "l3_rag_only", "l4_human_intervention": "l4_human_intervention",
        "end": END,
    }

    def __init__(self, analyzer=None, executor=None, *, max_retry_attempts: int = 3,
                 checkpointer=None) -> None:
        self._analyzer = analyzer or FaultAnalyzer()
        self._executor = executor or FaultExecutor()
        self.max_retry_attempts = max_retry_attempts
        self._checkpointer = checkpointer
        self._compiled_graph: CompiledStateGraph | None = None

    # ---------- 节点 ----------
    async def _analyze_fault(self, state: FaultRecoveryState) -> dict[str, Any]:
        """故障分析：产出诊断，决定起始档位（可重试→L1；不可重试但有降级→L2…）。"""
        raw = state.get("fault_context")
        if not raw:
            raise ValueError("缺少 fault_context")
        ctx = _ctx_from_dict(raw)
        diag = (_diag_from_dict(state["diagnosis"]) if state.get("diagnosis")
                else self._analyzer.analyze(ctx))
        if diag.retry_feasible and self._executor.retry is not None:
            level = 1
        elif self._executor.fallback is not None:
            level = 2
        elif self._executor.rag_only is not None:
            level = 3
        else:
            level = 4
        return {"diagnosis": _diag_to_dict(diag), "level": level, "attempts": 0,
                "human_rounds": 0, "resolved": False, "output": "",
                "error_message": diag.root_cause, "human_decision": "",
                "trail": "analyze_fault"}

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
        if self._executor.retry is None:
            return {"level": 2, "trail": "l1_retry:skip"}
        ctx = _ctx_from_dict(state["fault_context"])
        n = int(state.get("attempts", 0))
        attempt = await self._executor.retry(ctx, n)
        if attempt.success:
            return {"resolved": True, "output": attempt.output, "trail": "l1_retry:ok"}
        n += 1
        if n >= self.max_retry_attempts:
            return {"level": 2, "attempts": n, "trail": "l1_retry:fail->L2"}
        return {"attempts": n, "trail": "l1_retry:fail->retry"}

    async def _l2_adjust(self, state: FaultRecoveryState) -> dict[str, Any]:
        """L2 调参降级；失败升 L3。"""
        if self._executor.fallback is None:
            return {"level": 3, "trail": "l2_adjust:skip"}
        ctx = _ctx_from_dict(state["fault_context"])
        diag = _diag_from_dict(state["diagnosis"])
        attempt = await self._executor.fallback(ctx, diag)
        if attempt.success:
            return {"resolved": True, "output": attempt.output, "trail": "l2_adjust:ok"}
        return {"level": 3, "error_message": attempt.output or diag.root_cause,
                "trail": "l2_adjust:fail->L3"}

    async def _l3_rag_only(self, state: FaultRecoveryState) -> dict[str, Any]:
        """L3 仅 RAG 兜底；失败升 L4。"""
        if self._executor.rag_only is None:
            return {"level": 4, "trail": "l3_rag_only:skip"}
        ctx = _ctx_from_dict(state["fault_context"])
        diag = _diag_from_dict(state["diagnosis"])
        attempt = await self._executor.rag_only(ctx, diag)
        if attempt.success:
            return {"resolved": True, "output": attempt.output, "trail": "l3_rag_only:ok"}
        return {"level": 4, "trail": "l3_rag_only:fail->L4"}

    async def _l4_human_intervention(self, state: FaultRecoveryState) -> dict[str, Any]:
        """L4 人工干预：LangGraph 原生 HITL（interrupt()）。

        节点在此暂停（ainvoke 正常返回、state.next 指向本节点）；人工用
        Command(resume=...) 恢复：retry→回 L1；skip/abort→结束。
        """
        diag_raw = state.get("diagnosis") or {}
        task_id = str(state.get("task_id", ""))
        payload: dict[str, Any] = {
            "type": "fault_recovery_hitl", "task_id": task_id,
            "fault_type": str(diag_raw.get("fault_type", "unknown")),
            "severity": str(diag_raw.get("severity", "unknown")),
            "root_cause": str(diag_raw.get("root_cause") or state.get("error_message", ""))[:300],
            "suggestions": list(diag_raw.get("recovery_suggestions", [])),
            "options": [HUMAN_RETRY, HUMAN_SKIP, HUMAN_ABORT],
            "human_rounds": int(state.get("human_rounds", 0)) + 1,
        }
        decision = str(interrupt(payload) or HUMAN_SKIP).strip().lower()
        rounds = int(state.get("human_rounds", 0)) + 1
        base = {"human_decision": decision, "human_rounds": rounds}
        if decision == HUMAN_RETRY:
            return {**base, "level": 1, "attempts": 0, "trail": "l4_human_intervention:retry"}
        if decision == HUMAN_ABORT:
            return {**base, "resolved": True, "output": f"任务 {task_id} 已由人工中止。",
                    "trail": "l4_human_intervention:abort"}
        return {**base, "resolved": True, "output": f"任务 {task_id} 已由人工跳过，保留待处理。",
                "trail": "l4_human_intervention:skip"}

    # ---------- 图构建 ----------
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

    def _compiled(self) -> CompiledStateGraph:
        if self._compiled_graph is None:
            self._compiled_graph = self.build().compile(
                checkpointer=self._checkpointer or MemorySaver())
        return self._compiled_graph

    def diagram(self) -> str:
        """Mermaid 图 —— 流程可视化（复盘/文档）。"""
        return self._compiled().get_graph().draw_mermaid()

    # ---------- 运行 ----------
    @staticmethod
    def _initial_state(ctx: FaultContext) -> dict[str, Any]:
        return {"request_id": ctx.request_id, "task_id": ctx.task_id,
                "fault_context": _ctx_to_dict(ctx), "level": 0, "attempts": 0,
                "human_rounds": 0, "trail": [], "resolved": False, "output": "",
                "error_message": "", "human_decision": ""}

    @staticmethod
    def _to_outcome(values: dict[str, Any], *, status: str, thread_id: str,
                    human_question: dict[str, Any] | None = None) -> RecoveryOutcome:
        diag_raw = values.get("diagnosis")
        return RecoveryOutcome(
            status=status, resolved=bool(values.get("resolved", False)),
            output=str(values.get("output", "")), level=int(values.get("level", 0)),
            attempts=int(values.get("attempts", 0)),
            human_rounds=int(values.get("human_rounds", 0)),
            trail=list(values.get("trail", [])),
            diagnosis=_diag_from_dict(diag_raw) if diag_raw is not None else None,
            thread_id=thread_id, human_question=human_question)

    async def run(self, ctx: FaultContext, *, thread_id: str = "default",
                  resume: str | None = None,
                  recursion_limit: int = DEFAULT_RECURSION_LIMIT) -> RecoveryOutcome:
        """执行/恢复一次故障恢复流程。

        resume=None 开始新流程；否则以人工决策恢复 L4 暂停点。
        返回 status=awaiting_human 表示停在 L4，等待 resume。
        """
        graph = self._compiled()
        config: RunnableConfig = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": recursion_limit,
        }
        if resume is None:
            await graph.ainvoke(self._initial_state(ctx), config)
        else:
            await graph.ainvoke(Command(resume=resume), config)
        # LangGraph 1.x：节点调 interrupt() 时 ainvoke 正常返回，靠 state.next
        # 判断是否停在 L4；human_question 从 tasks.interrupts 读取。
        snapshot = await graph.aget_state(config)
        if snapshot.next:
            outcome = self._to_outcome(snapshot.values, status="awaiting_human",
                                       thread_id=thread_id)
            for task in snapshot.tasks or []:
                if task.interrupts:
                    outcome.human_question = task.interrupts[0].value
                    break
            return outcome
        return self._to_outcome(snapshot.values, status="completed", thread_id=thread_id)


# ==========================================================================
# 只读 demo：注入 fake 执行器，跑通 条件边 + 循环边 + HITL
# 运行：python -m app.infrastructure.agent.langgraph_fault_recovery
# ==========================================================================
def _demo_executor(*, retry_results: list[bool], fallback_ok: bool = False,
                   rag_ok: bool = False) -> FaultExecutor:
    async def retry(ctx: FaultContext, n: int) -> RecoveryAttempt:
        ok = retry_results[min(n, len(retry_results) - 1)] if retry_results else False
        return RecoveryAttempt(ok, f"[L1 retry#{n}] {ctx.task_id}")

    async def fallback(ctx: FaultContext, diag: FaultDiagnosis) -> RecoveryAttempt:
        return RecoveryAttempt(fallback_ok, f"[L2] {ctx.task_id}")

    async def rag_only(ctx: FaultContext, diag: FaultDiagnosis) -> RecoveryAttempt:
        return RecoveryAttempt(rag_ok, f"[L3 RAG] {ctx.task_id}")

    return FaultExecutor(retry=retry, fallback=fallback, rag_only=rag_only)


def _demo_ctx(tid: str, *, msg: str) -> FaultContext:
    return FaultContext(request_id=f"req-{tid}", task_id=tid, agent_id="router_agent",
                        tool_name="query_customer_info", error_message=msg,
                        error_type="RuntimeError", retry_count=0, elapsed_time_ms=120.0)


async def demo() -> None:
    print("== 图结构（Mermaid）==")
    print(FaultRecoveryWorkflow().diagram())

    async def go(tid: str, msg: str, wf: FaultRecoveryWorkflow, resume: str | None = None):
        o = await wf.run(_demo_ctx(tid, msg=msg), thread_id=f"demo-{tid}", resume=resume)
        print(f"{o.status:>13} | level={o.level} resolved={o.resolved} | {o.trail}")
        return o

    print("\n1) L1 第1次成功：")
    await go("t1", "tool timeout", FaultRecoveryWorkflow(
        executor=_demo_executor(retry_results=[True])))

    print("\n2) L1 循环：失败1次后成功（循环边）：")
    await go("t2", "network error", FaultRecoveryWorkflow(
        executor=_demo_executor(retry_results=[False, True])))

    print("\n3) L1 用尽→L2 备用成功（条件边）：")
    await go("t3", "tool timeout", FaultRecoveryWorkflow(
        executor=_demo_executor(retry_results=[False, False, False], fallback_ok=True)))

    print("\n4) L1→L2 失败→L3 仅RAG 成功：")
    await go("t4", "network error", FaultRecoveryWorkflow(
        executor=_demo_executor(retry_results=[False, False, False], rag_ok=True)))

    print("\n5) 全失败→L4 人工（interrupt 暂停）→ resume=skip：")
    wf = FaultRecoveryWorkflow(executor=_demo_executor(
        retry_results=[False, False, False]))
    waiting = await go("t5", "unknown", wf)
    assert waiting.status == "awaiting_human" and waiting.human_question
    print("   人工选择 [skip] ...")
    done = await go("t5", "unknown", wf, resume="skip")
    assert done.resolved


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(demo())
```

### 注解要点（LangGraph 三要素）

- **State（状态）**：`FaultRecoveryState(TypedDict)` 里 `Annotated[list[str], _append_trail]`
  是 **reducer 通道**（追加而非覆盖），其余是普通覆盖通道。
- **Node（节点）**：`_analyze_fault / _l1_retry / _l2_adjust / _l3_rag_only /
  _l4_human_intervention` —— 每个节点只返回要改的字段。
- **Edge（边）**：
  - `add_edge(START, "analyze_fault")` —— 固定边；
  - `add_conditional_edges(src, _route, ...)` —— **条件边**；同档重试就是**循环边**
    （`_route` 又指回同一节点），升级就是**条件边**（`level+1` 指向下一档）。
- **HITL**：`interrupt(payload)` 让流程暂停；恢复用 `Command(resume="skip"/"retry")`。
- **Checkpointer**：`MemorySaver()` 演示断点恢复；生产换
  `langgraph-checkpoint-postgres` 的 `PostgresSaver`（pyproject 已引入）。
- **可序列化约定**：State 只放普通 dict（dataclass 用 `_ctx_to_dict / _diag_to_dict`
  转换），否则 msgpack Checkpoint 会告警（未来版本直接报错）。

---

## 三、文件 2（可选）：`tests/unit_tests/test_langgraph_fault_recovery.py`

用 `asyncio.run` 驱动（不依赖 pytest-asyncio），覆盖条件边、循环边、HITL。

```python
import asyncio
from app.infrastructure.agent.langgraph_fault_recovery import (
    FaultExecutor, FaultRecoveryWorkflow, RecoveryAttempt,
    HUMAN_RETRY, HUMAN_SKIP,
)
from app.infrastructure.fault.fault_types import FaultContext, FaultDiagnosis


def _ctx(tid="t", msg="tool timeout"):
    return FaultContext(request_id=f"r-{tid}", task_id=tid, agent_id="a",
                        tool_name="t", error_message=msg, error_type="RuntimeError",
                        retry_count=0, elapsed_time_ms=1.0)


def _exec(retry_results=(False, False, False), fallback_ok=False, rag_ok=False):
    async def retry(ctx, n):
        ok = retry_results[min(n, len(retry_results) - 1)] if retry_results else False
        return RecoveryAttempt(ok, f"retry{n}")
    async def fallback(ctx, d):
        return RecoveryAttempt(fallback_ok, "fallback")
    async def rag(ctx, d):
        return RecoveryAttempt(rag_ok, "rag")
    return FaultExecutor(retry=retry, fallback=fallback, rag_only=rag)


def test_l1_loop_then_success():          # 循环边
    wf = FaultRecoveryWorkflow(executor=_exec(retry_results=(False, True)))
    o = asyncio.run(wf.run(_ctx("b", "network error")))
    assert o.resolved and o.trail[-1] == "l1_retry:ok"
    assert "l1_retry:fail->retry" in o.trail


def test_escalate_to_l2():                # 条件边升级
    wf = FaultRecoveryWorkflow(executor=_exec(retry_results=(False, False, False), fallback_ok=True))
    o = asyncio.run(wf.run(_ctx("c")))
    assert o.resolved and o.level == 2 and o.trail[-1] == "l2_adjust:ok"


def test_escalate_to_l3_rag():            # L1→L2→L3
    wf = FaultRecoveryWorkflow(executor=_exec(retry_results=(False, False, False), rag_ok=True))
    o = asyncio.run(wf.run(_ctx("d", "network error")))
    assert o.resolved and o.level == 3 and o.trail[-1] == "l3_rag_only:ok"


def test_hitl_skip():
    wf = FaultRecoveryWorkflow(executor=_exec(retry_results=(False, False, False)))
    w = asyncio.run(wf.run(_ctx("e"), thread_id="e"))
    assert w.status == "awaiting_human" and w.human_question
    o = asyncio.run(wf.run(_ctx("e"), thread_id="e", resume=HUMAN_SKIP))
    assert o.resolved and o.trail[-1] == "l4_human_intervention:skip"


def test_hitl_retry_back_to_l1():
    calls = [0]

    async def retry(ctx, n):
        calls[0] += 1
        return RecoveryAttempt(calls[0] >= 4, f"call{calls[0]}")

    wf = FaultRecoveryWorkflow(executor=FaultExecutor(
        retry=retry, fallback=_exec(retry_results=(False,)).fallback,
        rag_only=_exec(retry_results=(False,)).rag_only))
    w = asyncio.run(wf.run(_ctx("f"), thread_id="f"))
    assert w.status == "awaiting_human"
    o = asyncio.run(wf.run(_ctx("f"), thread_id="f", resume=HUMAN_RETRY))
    assert o.resolved and "l4_human_intervention:retry" in o.trail and o.trail[-1] == "l1_retry:ok"
```

运行：

```bash
uv run --group dev pytest tests/unit_tests/test_langgraph_fault_recovery.py
```

---

## 四、后续如何接主流程（A1 扩散，暂不需要）

试点验证通过后，把 `ChatService._aexecute_decomposed_tasks` 里的手写重试阶梯，
替换为注入真实执行器的图调用：

```python
from app.infrastructure.agent.langgraph_fault_recovery import (
    FaultExecutor, FaultRecoveryWorkflow, RecoveryAttempt, retry_backoff_seconds,
)

async def _retry(ctx, n):           # 真实：带退避重试子任务
    backoff = retry_backoff_seconds(diag)  # 用 diagnosis 映射退避秒数
    # ... 执行子任务 ...
    return RecoveryAttempt(ok, out)

async def _fallback(ctx, diag):     # 真实：切备用模型 / 备用工具
    # ...
    return RecoveryAttempt(ok, out)

async def _rag_only(ctx, diag):     # 真实：retriever.retrieve + 纯 RAG 总结
    # ...
    return RecoveryAttempt(ok, out)

recovery = FaultRecoveryWorkflow(executor=FaultExecutor(
    retry=_retry, fallback=_fallback, rag_only=_rag_only))

outcome = await recovery.run(fault_ctx, thread_id=request_id)
if outcome.status == "awaiting_human":
    decision = await 人工决策(outcome.human_question)   # 复用 intervention_handler 的队列
    outcome = await recovery.run(fault_ctx, thread_id=request_id, resume=decision)
```

> 要点：**简单链路（单 Agent 工具调用、纯 RAG 问答）保持现状，不图化**
> —— 这是 `ENTERPRISE_EVALUATION-v2.md` 里明确的取舍。

---

## 五、运行验证清单

- [ ] 模块可运行：`python -m app.infrastructure.agent.langgraph_fault_recovery`（打印 Mermaid + 5 个场景）
- [ ] 单测通过：`uv run --group dev pytest tests/unit_tests/test_langgraph_fault_recovery.py`
- [ ] 确认 State 里没有 dataclass 对象（避免 msgpack 告警）
- [ ] 人工决策三态验证：`retry` 回 L1、`skip` 结束、`abort` 结束

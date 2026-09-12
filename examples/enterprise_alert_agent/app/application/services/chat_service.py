import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Generator
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, TypedDict, cast

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langsmith.run_trees import RunTree

from app.config.dynamic_settings import ConfigManager
from app.config.settings import settings
from app.infrastructure.agent.a2a_protocol import (
    A2AProtocol,
    AgentTaskExecutionRequest,
    AgentTaskExecutionResult,
    IntentClassification,
    ParallelTaskResult,
    SubTask,
    TaskDecomposition,
    ToolSelection,
)
from app.infrastructure.agent.agent_coordinator import MultiAgentOrchestrator
from app.infrastructure.agent.agent_registry import AgentDescriptor, AgentRegistry
from app.infrastructure.agent.intervention_handler import InterventionHandler
from app.infrastructure.agent.langgraph_fault_recovery import (
    FaultExecutor,
    FaultRecoveryWorkflow,
    RecoveryAttempt,
    retry_backoff_seconds,
)
from app.infrastructure.agent.langgraph_reflection import (
    ReflectionExecutor,
    ReflectionWorkflow,
    ReviewDecision,
)
from app.infrastructure.cache.multi_tier_cache import multi_tier_cache
from app.infrastructure.fault.fault_analyzer import FaultAnalyzer
from app.infrastructure.fault.fault_types import FaultContext
from app.infrastructure.llm.context_budget import trim_text
from app.infrastructure.llm.model_client import (
    BudgetExceededError,
    ModelAuthError,
    ModelClient,
    ModelRequestError,
)
from app.infrastructure.mcp.mcp_client import get_tools_metadata
from app.infrastructure.memory.redis_postgres_conversation_memory import (
    MemoryScope,
    RedisPostgresConversationMemoryStore,
    TaskStatus,
)
from app.infrastructure.queue.dlq_handler import dead_letter_queue
from app.infrastructure.skill.registry import skill_registry
from app.observability.alert_manager import AlertManager
from app.observability.alert_types import AlertSeverity, AlertTypes
from app.observability.langsmith_tracer import LangSmithTracer
from app.observability.metrics import MetricsCollector
from app.rag.retrieval.retriever import Retriever
from app.schemas.chat import ChatRequest, ChatResponse, Citation
from app.tool.static import _safe_parse_intent_json, _to_bool

logger = logging.getLogger(__name__)

SkillFunc = Callable[..., dict[str, Any]]


@dataclass
class AgentState:
    answer: str = ""
    current_turn_count: int = 0
    token_counter: list[int] = field(default_factory=lambda: [0])
    previous_tool_calls: set[str] = field(default_factory=set)
    read_only_mode: bool = False
    tool_error_count: int = 0
    "任务分解结果"
    task_decomposition: TaskDecomposition | None = None
    is_multi_task: bool = False
    parallel_task_results: ParallelTaskResult | None = None
    failed_task_ids: list[str] = field(default_factory=list)
    retry_count: int = 0
    fallback_used: bool = False
    fallback_strategy: str = ""
    active_agent_id: str = "router_agent"
    assigned_agent_ids: list[str] = field(default_factory=list)
    reflection_rounds: int = 0
    reflection_approved: bool = False
    reflection_trail: list[str] = field(default_factory=list)


class ToolsResolution(TypedDict):
    available: list[dict[str, Any]]
    skill_map: dict[str, SkillFunc]
    mcp_map: dict[str, SkillFunc]


class ChatService:
    def __init__(
        self,
        model_client: ModelClient,
        retriever: Retriever,
        trace: LangSmithTracer,
        memory: RedisPostgresConversationMemoryStore,
        _intervention_handler: InterventionHandler,
        _alert_manager: AlertManager,
        _metrics_collector: MetricsCollector,
        agent_registry: AgentRegistry | None = None,
        orchestrator: MultiAgentOrchestrator | None = None,
    ) -> None:
        self.model_client = model_client
        self.retriever = retriever
        self.trace = trace
        self.memory = memory
        self.agent_registry = agent_registry or AgentRegistry()
        self.orchestrator = orchestrator or MultiAgentOrchestrator(
            self.agent_registry, A2AProtocol()
        )
        self.fault_analyzer = FaultAnalyzer()
        self._intervention_handler = _intervention_handler
        self.alert_manager = _alert_manager
        self.metrics_collector = _metrics_collector
        self.config_manager = ConfigManager()
        self.max_retries = self.config_manager.get_agent_max_iterations()
        self.task_timeout = self.config_manager.get_task_timeout()
        self.max_parallel_tasks = self.config_manager.get_task_max_workers()

    async def aask_stream(
        self, req: ChatRequest, *, parent_run: RunTree | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """彻底异步版本：全部 LLM/工具/记忆调用均为非阻塞。"""

        start_time = time.perf_counter()
        request_id = str(uuid.uuid4())
        trace_id = request_id
        ask_run = self.trace.start_child(
            name="service.chat.aask_stream",
            run_type="chain",
            inputs={"query": req.query, "business_context": req.business_context},
            tags=["service", "chat", "stream", "async"],
            metadata={
                "request_id": request_id,
                "trace_id": trace_id,
                "tenant_id": req.tenant_id,
                "user_id": req.user_id,
                "thread_id": req.thread_id,
            },
            parent_run=parent_run,
        )
        self.metrics_collector.create_metrics(request_id=request_id)
        logger.info(
            "[%s] Chat aask_stream start: tenant=%s user=%s thread=%s query=%s",
            request_id,
            req.tenant_id,
            req.user_id,
            req.thread_id,
            str(req.query)[:200],
        )

        def _yield_text_chunks(
            text: str, chunk_size: int = 24
        ) -> Generator[dict[str, Any], None, None]:
            for i in range(0, len(text), chunk_size):
                yield {
                    "type": "token",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "content": text[i : i + chunk_size],
                }

        try:
            intent_classification, module_activation = await self._aclassify_intent(
                req.query, self.model_client, parent_run=ask_run
            )
            tool_selection: ToolSelection | None = None
            tools_resolution = await self._aresolve_tools(module_activation)
            available_tools: list[dict[str, Any]] = tools_resolution["available"]
            SKILL_MAP: dict[str, SkillFunc] = tools_resolution["skill_map"]
            mcp_tool_map: dict[str, SkillFunc] = tools_resolution["mcp_map"]
            tool_selection = await self._aselect_tool(
                query=req.query,
                intent_classification=intent_classification,
                available_tools=available_tools,
                parent_run=ask_run,
            )
            state = AgentState()
            select_agent = self._select_agent(
                intent_classification=intent_classification,
                tool_selection=tool_selection,
            )
            state.active_agent_id = select_agent.agent_id
            state.assigned_agent_ids.append(select_agent.agent_id)

            cached = await asyncio.to_thread(multi_tier_cache.get, req.query, req.tenant_id)
            if cached:
                logger.info("[%s] Cache hit: returning cached answer", request_id)
                cache_response = ChatResponse(**cached)
                cache_response.request_id = request_id
                cache_response.trace_id = trace_id
                self.metrics_collector.record_cache_hit(request_id=request_id)
                self.trace.end_run(
                    ask_run,
                    outputs={
                        "request_id": request_id,
                        "cache_hit": True,
                        "is_multi_task": False,
                        "failed_task_count": 0,
                    },
                )
                yield {
                    "type": "done",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "answer": cache_response.answer,
                    "citations": [c.model_dump() for c in cache_response.citations],
                    "model": cache_response.model,
                    "intent": cache_response.intent,
                    "intent_confidence": cache_response.intent_confidence,
                    "selected_tool": cache_response.selected_tool,
                    "tool_confidence": cache_response.tool_confidence,
                    "fallback_tool": cache_response.fallback_tool,
                    "tool_selection_reason": cache_response.tool_selection_reason,
                    "task_decomposed": cache_response.task_decomposed,
                    "is_multi_task": cache_response.is_multi_task,
                    "multi_task_results": cache_response.multi_task_results,
                    "failed_tasks": cache_response.failed_tasks,
                    "manual_intervention_required": cache_response.manual_intervention_required,
                    "retry_count": cache_response.retry_count,
                    "fallback_used": cache_response.fallback_used,
                    "fallback_strategy": cache_response.fallback_strategy,
                    "active_agent_id": cache_response.active_agent_id,
                    "assigned_agent_ids": cache_response.assigned_agent_ids,
                    "performance_metrics": cache_response.performance_metrics,
                }
                return
            self.metrics_collector.record_cache_miss(request_id=request_id)

            history_scope = MemoryScope(
                tenant_id=req.tenant_id,
                user_id=req.user_id,
                thread_id=req.thread_id,
            )
            memory_context = await self.memory.aload_context(history_scope, max_turns=5)
            history_prompt_context = trim_text(
                memory_context.as_prompt_text(), settings.context_history_token_budget, keep="tail"
            )
            logger.info(
                "[%s] Context budget: history_tokens_estimated=%d",
                request_id,
                len(history_prompt_context) // 2,
            )

            context = ""
            docs: list[dict[str, str]] = []
            citations: list[Citation] = []
            if not self.is_query_time(req.query) and module_activation.get("rag", True):
                docs = await asyncio.to_thread(
                    self.retriever.retrieve,
                    req.query,
                    top_k=settings.retrieval_final_k,
                    history_text=history_prompt_context,
                    where=None,
                    parent_run=ask_run,
                )
                docs = docs[: settings.context_top_k]
                raw_context = "\n".join([f"[{d['source_id']}] {d['content']}" for d in docs])
                context = trim_text(
                    raw_context, settings.context_retrieval_token_budget, keep="head"
                )
                citations = [Citation(source_id=d["source_id"], snippet=d["content"]) for d in docs]
                logger.info(
                    "[%s] Context budget: docs=%d context_chars=%d",
                    request_id,
                    len(docs),
                    len(context),
                )

            system_prompt = self._build_base_system_prompt(
                context=context,
                history_prompt_text=history_prompt_context,
                query=req.query,
                mcp_tool_map=mcp_tool_map,
                tool_selection=tool_selection,
            )
            task_decomposition = self._decompose_task(
                query=req.query,
                intent_classification=intent_classification,
                parent_run=ask_run,
            )
            state.task_decomposition = task_decomposition
            state.is_multi_task = len(task_decomposition.subtasks) > 1

            if state.is_multi_task:
                parallel_results = await self._aexecute_decomposed_tasks(
                    req_query=req.query,
                    request_id=request_id,
                    decomposition=task_decomposition,
                    system_prompt=system_prompt,
                    available_tools=available_tools,
                    skill_map=SKILL_MAP,
                    history_scope=history_scope,
                    mcp_tool_map=mcp_tool_map,
                    tool_selection=tool_selection,
                    state=state,
                    parent_run=ask_run,
                )
                state.parallel_task_results = parallel_results
                state.failed_task_ids = parallel_results.failed_task_ids
                answer = self._merge_multi_task_results(parallel_results)
                if settings.reflection_enabled:
                    answer = await self._areflect_multi_agent_answer(
                        query=req.query,
                        draft_answer=answer,
                        task_result=parallel_results,
                        agentState=state,
                        parent_run=ask_run,
                    )

                for failed_task_id in parallel_results.failed_task_ids:
                    await dead_letter_queue.add(
                        task_id=failed_task_id,
                        payload={"request_id": request_id},
                        failure_reason="parallel_task_execution_failed",
                    )
                    if parallel_results.failed_tasks >= settings.agent_tool_failure_threshold:
                        self._intervention_handler.create_intervention_request(
                            task_id=failed_task_id,
                            reason="parallel_task_execution_failed。",
                            user_id=req.user_id,
                        )
            else:
                if tool_selection and tool_selection.tool_name:
                    preferred_tools = next(
                        (t for t in available_tools if t.get("name") == tool_selection.tool_name),
                        None,
                    )
                    if preferred_tools:
                        available_tools.remove(preferred_tools)
                        available_tools.insert(0, preferred_tools)
                answer = await self._arun_agent_loop(
                    req.query,
                    request_id,
                    system_prompt,
                    available_tools,
                    SKILL_MAP,
                    mcp_tool_map,
                    state,
                    tool_selection,
                    parent_run=ask_run,
                )

            if not answer:
                summary_prompt = (
                    system_prompt
                    + "\n\n请基于上面的知识库和工具结果，输出最终结论。"
                    + "\n要求："
                    + "\n1) 给出明确结论；"
                    + "\n2) 如果工具失败或数据不足，明确说明不确定性；"
                    + "\n3) 不要再调用任何工具。"
                )
                fallback_answer = await self.model_client.achat(
                    user_query=req.query,
                    system_prompt=summary_prompt,
                    tools=[],
                    return_message=False,
                    parent_run=ask_run,
                    _token_counter=state.token_counter,
                )
                answer = str(fallback_answer or "").strip()

            final_answer = str(answer or "").strip()
            if final_answer:
                for chunk in _yield_text_chunks(final_answer):
                    yield chunk
            else:
                answer_parts: list[str] = []
                async for piece in self.model_client.astream_chat(
                    req.query,
                    system_prompt,
                    tools=[],
                    parent_run=ask_run,
                ):
                    answer_parts.append(piece)
                    yield {
                        "type": "token",
                        "content": piece,
                        "request_id": request_id,
                        "trace_id": trace_id,
                    }
                final_answer = "".join(answer_parts).strip() or "未能在限定轮次内生成最终答案。"

            skip_memory_write = settings.time_query_skip_memory_write and self.is_query_time(
                req.query
            )
            if not skip_memory_write:
                await self.memory.aappend_turn(
                    history_scope,
                    role="user",
                    content=req.query,
                    metadata={
                        "request_id": request_id,
                        "current_turn_count": memory_context.turn_count + 1,
                    },
                )
                await self.memory.aappend_turn(
                    history_scope,
                    role="assistant",
                    content=final_answer,
                    metadata={
                        "request_id": request_id,
                        "citations_count": len(citations),
                    },
                )
                after_turn_count = memory_context.turn_count + 2
                if after_turn_count >= settings.memory_summary_update_turn_threshold:
                    summary_text = await self.abuild_memory_summary(
                        history_prompt_text=history_prompt_context,
                        user_query=req.query,
                        answer=final_answer,
                        parent_run=ask_run,
                    )
                    await self.memory.asave_summary(history_scope, summary_text)

            elapsed_time = time.perf_counter() - start_time
            self.metrics_collector.record_latency(
                request_id=request_id, latency_ms=elapsed_time * 1000
            )
            metrics = self.metrics_collector.get_metrics(request_id=request_id)
            performance_metrics = {
                "total_time_ms": elapsed_time * 1000,
                "p50_latency_ms": metrics.get_p50_latency() if metrics else 0,
                "p95_latency_ms": metrics.get_p95_latency() if metrics else 0,
                "p99_latency_ms": metrics.get_p99_latency() if metrics else 0,
                "token_usage": metrics.token_usage if metrics else 0,
                "estimated_cost_usd": metrics.estimated_cost_usd if metrics else 0,
                "cache_hit_rate": metrics.get_cache_hit_rate() if metrics else 0,
                "success_rate": metrics.get_success_rate() if metrics else 1.0,
            }

            resp = ChatResponse(
                answer=final_answer,
                citations=citations,
                model=settings.model_name,
                request_id=request_id,
                intent=intent_classification.intent,
                intent_confidence=intent_classification.confidence,
                trace_id=trace_id,
                selected_tool=None if not tool_selection else tool_selection.tool_name,
                tool_confidence=None if not tool_selection else tool_selection.confidence,
                fallback_tool=[] if not tool_selection else tool_selection.fallback_tools,
                tool_selection_reason=None if not tool_selection else tool_selection.reasoning,
                task_decomposed=state.task_decomposition is not None,
                is_multi_task=state.is_multi_task,
                multi_task_results=None
                if not state.parallel_task_results
                else {
                    "completed_tasks": state.parallel_task_results.completed_tasks,
                    "failed_tasks": state.parallel_task_results.failed_tasks,
                    "total_tasks": state.parallel_task_results.total_tasks,
                    "total_time": round(state.parallel_task_results.total_time, 3),
                    "tools_used": state.parallel_task_results.tools_used,
                    "success_rate": round(
                        state.parallel_task_results.completed_tasks
                        / state.parallel_task_results.total_tasks,
                        3,
                    )
                    if state.parallel_task_results.total_tasks > 0
                    else 0.0,
                    "task_agent_mapping": state.parallel_task_results.task_agent_mapping,
                    "task_status_mapping": state.parallel_task_results.task_status_mapping,
                    "execution_batches": state.parallel_task_results.execute_batches,
                    "skipped_task_ids": state.parallel_task_results.skipped_task_ids,
                },
                failed_tasks=state.failed_task_ids,
                manual_intervention_required=len(state.failed_task_ids)
                >= settings.agent_tool_failure_threshold,
                retry_count=state.retry_count,
                fallback_used=state.fallback_used,
                fallback_strategy=state.fallback_strategy,
                active_agent_id=state.active_agent_id,
                assigned_agent_ids=state.assigned_agent_ids,
                performance_metrics=performance_metrics,
            )

            await asyncio.to_thread(
                multi_tier_cache.set, req.query, resp.model_dump(), req.tenant_id
            )

            self.trace.end_run(
                ask_run,
                outputs={
                    "request_id": request_id,
                    "retrieved_docs": len(docs),
                    "model": settings.model_name,
                    "is_multi_task": state.is_multi_task,
                    "retry_count": state.retry_count,
                    "fallback_used": state.fallback_used,
                    "failed_tasks_count": len(state.failed_task_ids),
                    "active_agent_id": state.active_agent_id,
                    "assigned_agent_ids": state.assigned_agent_ids,
                    "stream": True,
                },
            )

            yield {
                "type": "done",
                "request_id": request_id,
                "trace_id": trace_id,
                "answer": resp.answer,
                "citations": [c.model_dump() for c in resp.citations],
                "model": resp.model,
                "intent": resp.intent,
                "intent_confidence": resp.intent_confidence,
                "selected_tool": resp.selected_tool,
                "tool_confidence": resp.tool_confidence,
                "fallback_tool": resp.fallback_tool,
                "tool_selection_reason": resp.tool_selection_reason,
                "task_decomposed": resp.task_decomposed,
                "is_multi_task": resp.is_multi_task,
                "multi_task_results": resp.multi_task_results,
                "failed_tasks": resp.failed_tasks,
                "manual_intervention_required": resp.manual_intervention_required,
                "retry_count": resp.retry_count,
                "fallback_used": resp.fallback_used,
                "fallback_strategy": resp.fallback_strategy,
                "active_agent_id": resp.active_agent_id,
                "assigned_agent_ids": resp.assigned_agent_ids,
                "performance_metrics": resp.performance_metrics,
            }
        except BudgetExceededError as exc:
            self.metrics_collector.record_error(request_id=request_id)
            logger.exception("[%s] BudgetExceededError failed", request_id)
            self.trace.end_run(ask_run, error=LangSmithTracer.format_error(exc))
            yield {
                "type": "error",
                "code": "429",
                "message": "本次请求已超过模型 token 预算，请缩短问题或稍后重试",
                "request_id": request_id,
                "trace_id": trace_id,
            }
        except ModelAuthError as exc:
            self.metrics_collector.record_error(request_id=request_id)
            logger.exception("[%s] ModelAuthError failed", request_id)
            self.trace.end_run(ask_run, error=LangSmithTracer.format_error(exc))
            yield {
                "type": "error",
                "code": "401",
                "message": "模型鉴权失败，请检查 DASHSCOPE_API_KEY 是否正确且可用。",
                "request_id": request_id,
                "trace_id": trace_id,
            }
        except ModelRequestError as exc:
            self.metrics_collector.record_error(request_id=request_id)
            logger.exception("[%s] Chat aask_stream ModelRequestError failed", request_id)
            self.trace.end_run(ask_run, error=LangSmithTracer.format_error(exc))
            yield {
                "type": "error",
                "code": "502",
                "message": "模型服务请求失败，请稍后重试。",
                "request_id": request_id,
                "trace_id": trace_id,
            }
        except Exception as exc:
            self.metrics_collector.record_error(request_id=request_id)
            logger.exception("[%s] Chat aask_stream Exception failed", request_id)
            self.trace.end_run(ask_run, error=LangSmithTracer.format_error(exc))
            yield {
                "type": "error",
                "code": "500",
                "message": str(exc),
                "request_id": request_id,
                "trace_id": trace_id,
            }

    @staticmethod
    def tool_result_to_string(tool_result: dict[str, Any]) -> str:
        status = tool_result.get("status", "error")
        message = tool_result.get("message", "")
        error_code = tool_result.get("error_code", "")
        data_list = tool_result.get("data", [])
        if not isinstance(data_list, list):
            data_list = []

        row_count_raw = tool_result.get("row_count", tool_result.get("count", len(data_list)))
        try:
            row_count = int(row_count_raw)
        except (TypeError, ValueError):
            row_count = len(data_list)

        # 1. 检查状态是否成功
        if status != "success":
            return f"工具调用失败，状态: {status}, 消息: {message}, 错误码: {error_code}"
        # 2. 如果数据为空的处理
        if not data_list:
            return f"工具调用成功，但没有数据返回。消息: {message}"
        preview_rows = data_list[:5]
        formatted_rows: list[str] = []
        for row in preview_rows:
            if isinstance(row, dict):
                row_str = ", ".join([f"{key}: {value}" for key, value in row.items()])
                formatted_rows.append(f"- {row_str}")
            else:
                formatted_rows.append(f"- {row!s}")
        return f"工具调用成功，返回 {row_count} 行数据。前5行预览:\n" + "\n".join(formatted_rows)

    @staticmethod
    def trim_memory_by_turns(memory_text: str, max_turns: int = 5) -> str:
        if not memory_text:
            return ""
        lines = memory_text.splitlines()
        turns: list[str] = []
        current_turn: list[str] = []
        for line in lines:
            if line.startswith("User: ") and current_turn:
                turns.append("\n".join(current_turn))
                current_turn = [line]
            else:
                current_turn.append(line)
        if current_turn:
            turns.append("\n".join(current_turn))
        if len(turns) <= max_turns:
            return memory_text
        return "\n".join(turns[-max_turns:]).strip()

    @staticmethod
    def trim_answer_by_length(text: str, max_length: int = 1000) -> str:
        if not text or len(text) <= max_length:
            return text or ""
        return text[:max_length].strip()

    @staticmethod
    def is_query_time(text: str) -> bool:
        if not text:
            return False
        time_keywords = [
            "今天",
            "日期",
            "几号",
            "星期",
            "几点",
            "当前时间",
            "today",
            "date",
            "day",
            "time",
            "weekday",
        ]
        return any(keyword in text for keyword in time_keywords)

    async def abuild_memory_summary(
        self,
        history_prompt_text: str,
        user_query: str,
        answer: str,
        parent_run: RunTree | None = None,
    ) -> str:
        summary_prompt = (
            f"请基于以下历史对话记忆、当前用户提问和智能体回答，生成一个简洁的对话摘要，供后续检索使用。"
            f"\n要求："
            f"\n1) 摘要内容必须包含用户提问和智能体回答的核心信息；"
            f"\n2) 摘要尽量简洁，控制在200字以内；"
            f"\n3) 不要包含无关细节或客套话；"
            f"\n4) 直接输出摘要内容，不要任何额外说明。"
            f"\n\n历史对话记忆:\n{history_prompt_text}"
            f"\n\n当前用户提问:\n{user_query}"
            f"\n\n智能体回答:\n{answer}"
        )
        logger.debug("Building memory summary (async), prompt length=%d", len(summary_prompt))
        summary = await self.model_client.achat(
            user_query="请生成长期记忆摘要",
            system_prompt=summary_prompt,
            tools=[],
            return_message=False,
            parent_run=parent_run,
            _token_counter=None,
        )
        return str(summary or "").strip()

    @staticmethod
    async def _aclassify_intent(
        query: str, model_client: ModelClient, parent_run: RunTree | None = None
    ) -> tuple[IntentClassification, dict[str, bool]]:
        classify_prompt = (
            "你是一个企业级意图分类器。请分析用户查询，并按以下格式返回 JSON：\n"
            "{\n"
            '  "intent": "意图类型，如 query_customer_info, create_order, update_record",\n'
            '  "confidence": 0.92,\n'
            '  "category": "retrieve|create|update|delete|other",\n'
            '  "entities": {"key": "value"},\n'
            '  "need_db": true,\n'
            '  "need_rag": true,\n'
            '  "reasoning": "分类理由"\n'
            "}\n\n"
            f"用户查询：{query}"
        )
        try:
            result = await model_client.achat(
                user_query=query,
                system_prompt=classify_prompt,
                tools=[],
                return_message=False,
                parent_run=parent_run,
                _token_counter=None,
            )
            parsed = _safe_parse_intent_json(str(result or "{}"))
            intent_classification = IntentClassification(
                intent=cast(str, parsed.get("intent", "unknown")),
                confidence=float(cast(str | float, parsed.get("confidence", 0.0))),
                category=cast(str, parsed.get("category", "other")),
                entities=cast(dict[str, Any] | None, parsed.get("entities", {})),
                reasoning=cast(str, parsed.get("reasoning", "")),
            )
            module_activation = {
                "mcp": _to_bool(parsed.get("need_db", True)),
                "rag": _to_bool(parsed.get("need_rag", True)),
            }
            logger.info(
                "Intent classified (async): intent=%s confidence=%.2f category=%s mcp=%s rag=%s",
                intent_classification.intent,
                intent_classification.confidence,
                intent_classification.category,
                module_activation["mcp"],
                module_activation["rag"],
            )
            return intent_classification, module_activation
        except Exception as e:  # noqa: BLE001 - fallback to enable all modules on classifier failure
            logger.warning(
                "Intent classification failed (async), falling back to all modules: %s", e
            )
            return IntentClassification(
                intent="unknown",
                confidence=0.0,
                category="other",
                entities={},
                reasoning="Intent classification failed",
            ), {"mcp": True, "rag": True}

    # 这点点对应参考文档:Agent&MCP工具注册与调用核心知识点
    async def _aresolve_tools(self, intent: dict[str, Any]) -> ToolsResolution:
        """异步版本：MCP 工具使用异步闭包，避免在异步上下文阻塞事件循环。"""
        from app.infrastructure.mcp.mcp_client import async_get_tool_map

        available_tools: list[dict[str, Any]] = list(skill_registry.metadata())
        mcp_tool_map: dict[str, SkillFunc] = {}
        mcp_tool_metadata: list = []
        try:
            if settings.mcp_enabled:
                # 异步调用MCP Client，拉取远端MCP服务暴露的工具映射（函数实现）
                mcp_tool_map = await async_get_tool_map()
                # 获取MCP工具描述元数据（给LLM看的function‑call schema）
                mcp_tool_metadata = get_tools_metadata()
                # 合并：本地工具 + MCP远端工具，全部给到LLM的可用工具列表
                available_tools.extend(mcp_tool_metadata)
        except Exception as exc:  # noqa: BLE001 - MCP failure falls back to local tools
            logger.warning("Failed to resolve MCP tools: %s", exc)
            mcp_tool_map = {}
            mcp_tool_metadata = []
        local_keys = set(skill_registry.skills_map().keys())
        mcp_keys = set(mcp_tool_map.keys())
        conflict_keys = local_keys & mcp_keys
        if conflict_keys:
            logger.warning(f"检测到工具名冲突，MCP将覆盖本地工具：{conflict_keys}")
        # 函数执行映射：本地skill_map 和 MCP工具map合并
        # key：工具名称；value：可直接await调用的处理函数
        skill_map = {**skill_registry.skills_map(), **mcp_tool_map}

        return ToolsResolution(
            available=available_tools,  # 给LLM：工具schema列表，用于function call
            skill_map=skill_map,  # 执行层：工具名 → 可执行函数
            mcp_map=mcp_tool_map,  # 单独保留MCP工具映射，上层可区分本地/远端
        )

    def _build_base_system_prompt(
        self,
        context: str,
        history_prompt_text: str,
        query: str,
        mcp_tool_map: dict[str, SkillFunc],
        tool_selection: ToolSelection | None = None,
    ) -> str:
        system_prompt = (
            "你是一个企业级智能体。\n"
            "请结合本地知识库指导，自主决定是否需要编写 SQL 语句查询本地数据库以获取最新的实时数据。\n"
            "如果不需要调用工具，直接给出结论。\n"
            f"知识库背景:\n{context}"
        )
        if tool_selection and tool_selection.confidence >= 0.5:
            system_prompt += (
                f"\n\n【工具选择】"
                f"\n- 已选择工具: {tool_selection.tool_name}"
                f"\n- 置信度: {tool_selection.confidence:.2f}"
                f"\n- 备选工具: {', '.join(tool_selection.fallback_tools) if tool_selection.fallback_tools else '无'}"
                f"\n- 选择理由: {tool_selection.reasoning or '无'}"
            )
            if tool_selection.fallback_tools:
                system_prompt += (
                    f"\n\n【备用工具使用规则】"
                    f"\n- 如果选择的工具调用失败或数据不足，请尝试使用以下备用工具: {', '.join(tool_selection.fallback_tools)}"
                    f"\n- 备用工具的调用顺序可根据实际情况灵活调整。"
                )

        if settings.time_tool_enabled:
            system_prompt += (
                "\n\n【硬规则】"
                "\n- 对日期/时间相关问题，必须调用 get_current_datetime 工具。"
                "\n- 禁止根据历史记忆、知识库或猜测回答日期时间。"
            )
        if history_prompt_text:
            system_prompt += f"\n\n历史对话记忆:\n{history_prompt_text}"
        return system_prompt

    async def _arun_agent_loop(
        self,
        query: str,
        request_id: str,
        system_prompt: str,
        available_tools: list[dict[str, Any]],
        skill_map: dict[str, SkillFunc],
        mcp_tool_map: dict[str, SkillFunc],
        state: AgentState,
        tool_selection: ToolSelection | None = None,
        parent_run: RunTree | None = None,
    ) -> str:
        answer = ""
        for iteration in range(self.config_manager.get_agent_max_iterations()):
            logger.info(
                "Agent iteration %d/%d (async, read_only=%s)",
                iteration + 1,
                self.config_manager.get_agent_max_iterations(),
                state.read_only_mode,
            )
            response_message = await self.model_client.achat(
                user_query=query,
                system_prompt=self._fit_agent_prompt(system_prompt),
                tools=available_tools if not state.read_only_mode else [],
                return_message=True,
                parent_run=parent_run,
                _token_counter=state.token_counter,
            )
            if hasattr(response_message, "usage"):
                self.metrics_collector.record_token_usage(
                    request_id=request_id,
                    tokens=response_message.usage.total_tokens,
                )

            tool_calls = getattr(response_message, "tool_calls", [])
            if not tool_calls:
                logger.info("Agent finished, no tool calls (async)")
                answer = response_message.content or ""
                break

            for tool_call in tool_calls:
                tool_name = tool_call.function.name if tool_call.function else ""
                tool_args = tool_call.function.arguments if tool_call.function else {}
                tool_signature = f"{tool_name}:{tool_args}"
                if tool_signature in state.previous_tool_calls:
                    logger.warning("Repeated tool call detected (async): %s", tool_signature[:200])
                    state.read_only_mode = True
                    break
                state.previous_tool_calls.add(tool_signature)

                logger.info("Agent calling tool (async): %s", tool_name)
                if tool_name not in skill_map:
                    state.tool_error_count += 1
                    error_msg = f"未知工具: {tool_name}，当前可调用工具: {list(skill_map.keys())}"
                    system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                    system_prompt = self._fit_agent_prompt(system_prompt)
                    if state.tool_error_count >= settings.agent_tool_failure_threshold:
                        state.read_only_mode = True
                        logger.warning("Tool failure threshold reached, forcing summary phase")
                    break

                actual_args: dict[str, Any] = {}
                try:
                    actual_args = tool_args if isinstance(tool_args, dict) else {}
                    if isinstance(tool_args, str):
                        parsed_args = json.loads(tool_args)
                        if not isinstance(parsed_args, dict):
                            raise TypeError("工具参数解析错误，应该是一个 JSON 对象。")
                        actual_args = parsed_args
                    if not isinstance(actual_args, dict):
                        raise TypeError("工具参数解析错误，应该是一个 JSON 对象。")
                    result = await self._aapply_tool(skill_map[tool_name], actual_args)
                    tool_summary = self._fit_tool_result(self.tool_result_to_string(result))
                    system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{tool_summary}"
                    system_prompt = self._fit_agent_prompt(system_prompt)
                    logger.debug("tool result (async): %s", tool_summary[:200])
                except json.JSONDecodeError:
                    state.tool_error_count += 1
                    error_msg = "工具调用失败：参数解析错误，不是合法的 JSON 字符串。"
                    system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                    system_prompt = self._fit_agent_prompt(system_prompt)
                    logger.warning("%s", error_msg)
                    if state.tool_error_count >= settings.agent_tool_failure_threshold:
                        state.read_only_mode = True
                        logger.warning("Tool failure threshold reached, forcing summary phase")
                        break
                except Exception as e:  # noqa: BLE001 - tool execution errors are converted to prompt feedback
                    state.tool_error_count += 1
                    error_msg = f"工具调用失败: {e}"
                    system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                    system_prompt = self._fit_agent_prompt(system_prompt)
                    logger.warning("%s", error_msg)

                    if (
                        tool_selection
                        and tool_selection.fallback_tools
                        and len(state.previous_tool_calls)
                        < self.config_manager.get_agent_max_iterations()
                    ):
                        logger.info(
                            "Attempting fallback tools (async): %s", tool_selection.fallback_tools
                        )
                        for backup_tool in tool_selection.fallback_tools:
                            if backup_tool not in skill_map:
                                logger.warning("Backup tool '%s' not available", backup_tool)
                                continue
                            try:
                                backup_result = await self._aapply_tool(
                                    skill_map[backup_tool], actual_args
                                )
                                backup_summary = self._fit_tool_result(
                                    self.tool_result_to_string(backup_result)
                                )
                                system_prompt += (
                                    f"\n\n工具调用结果 ({backup_tool} [备选]):\n{backup_summary}"
                                )
                                logger.info("Fallback tool '%s' succeeded", backup_tool)
                                state.tool_error_count -= 1
                                state.fallback_used = True
                                state.fallback_strategy = "tool_chain"
                                break
                            except Exception as backup_e:  # noqa: BLE001 - continue trying next fallback tool
                                logger.warning(
                                    "Fallback tool '%s' also failed: %s", backup_tool, backup_e
                                )
                                continue

                    if state.tool_error_count >= self.config_manager.get_tool_failure_threshold():
                        state.read_only_mode = True
                        logger.warning("Tool failure threshold reached, forcing summary phase")
                        break
        return answer

    async def _aselect_tool(
        self,
        query: str,
        intent_classification: IntentClassification,
        available_tools: list[dict[str, Any]],
        parent_run: RunTree | None = None,
    ) -> ToolSelection:
        tools_str = "\n".join(
            [f"- {t.get('name')}: {t.get('description', '')}" for t in available_tools]
        )
        tool_selection_prompt = (
            "你是一个企业级工具选择器。请根据用户查询和意图分类，选择最合适的工具。"
            "如果没有合适的工具，请返回空字符串。\n"
            "返回 JSON 格式：\n"
            "{\n"
            '  "tool_name": "选择的工具名称",\n'
            '  "confidence": 0.85,\n'
            '  "fallback_tools": ["备用工具1", "备用工具2"],\n'
            '  "reasoning": "选择理由"\n'
            "}\n\n"
            f"用户意图: {intent_classification.intent}\n"
            f"意图类型: {intent_classification.category}\n"
            f"用户查询: {query}\n\n"
            f"可用工具:\n{tools_str}\n\n"
        )
        try:
            result = await self.model_client.achat(
                user_query=query,
                system_prompt=tool_selection_prompt,
                tools=[],
                return_message=False,
                parent_run=parent_run,
                _token_counter=None,
            )
            parsed = _safe_parse_intent_json(str(result or "{}"))
            tool_selection = ToolSelection(
                tool_name=str(parsed.get("tool_name", "")),
                confidence=float(parsed.get("confidence", 0.0)),  # type: ignore
                fallback_tools=cast(list[str], parsed.get("fallback_tools", [])),
                reasoning=str(parsed.get("reasoning", "")),
            )
            logger.info(
                "Tool selected (async): %s (confidence=%.2f)",
                tool_selection.tool_name,
                tool_selection.confidence,
            )
            return tool_selection
        except Exception as e:  # noqa: BLE001 - fallback to default tool selection on any model error
            logger.warning("Tool selection failed (async), defaulting to no tool: %s", e)
            first_tool_name = cast(
                str, available_tools[0].get("name", "unknown") if available_tools else "unknown"
            )
            return ToolSelection(
                tool_name=first_tool_name,
                confidence=0.0,
                fallback_tools=[cast(str, t.get("name", "")) for t in available_tools[1:3]],
                reasoning=f"Tool selection failed: {e!s}",
            )

    def _select_agent(
        self,
        intent_classification: IntentClassification,
        tool_selection: ToolSelection | None = None,
    ) -> AgentDescriptor:
        """
        根据意图分类和工具选择结果，选择最合适的智能体
        Returns:
            AgentDescriptor
        """
        if tool_selection and tool_selection.tool_name:
            matched_agents = self.agent_registry.find_by_tool(tool_selection.tool_name)
            if matched_agents:
                return matched_agents[0]
        matched_agents = self.agent_registry.find_by_capability("intent_routing")
        if matched_agents:
            return matched_agents[0]
        # 如果没有匹配的智能体，返回默认智能体
        return AgentDescriptor(
            agent_id="router_agent",
            display_name="Router Agent",
            capabilities=["intent_routing"],
            supported_tools=[],
            priority=0,
        )

    @staticmethod
    def _split_query_to_subtasks(query: str) -> list[str]:
        """
        将复杂查询拆分为多个子任务
        Returns:
            list of subtask queries
        """
        text = query.strip()
        if not text:
            return []
        # 使用简单的分隔符拆分查询
        if len(text) < 40 and not any(
            k in text for k in ["和", "以及", "同时", "并且", ",", "，", ";", "；"]
        ):
            return [text]
        # 使用逗号、分号或中文分隔符拆分
        parts = re.split(r"(?:和|以及|同时|并且|,|，|;|；)", text)
        subtasks = [part.strip() for part in parts if part.strip()]
        return subtasks if subtasks else [text]

    def _decompose_task(
        self,
        query: str,
        intent_classification: IntentClassification,
        tool_selection: ToolSelection | None = None,
        parent_run: RunTree | None = None,
    ) -> TaskDecomposition:
        """
        将复杂任务分解为多个子任务
        Returns:
            TaskDecomposition
        """
        segments = self._split_query_to_subtasks(query)
        logger.info("Task decomposition: %d subtasks", len(segments))
        if len(segments) <= 1:
            return TaskDecomposition(
                subtasks=[SubTask(task_id="task_1", description=query, priority=0)],
                parallel_groups=[],
                dependencies={},
                strategy="single",
            )
        subtasks: list[SubTask] = []
        dependencies: dict[str, list[str]] = {}
        ids: list[str] = []
        sequential_keywords = [
            "然后",
            "接着",
            "之后",
            "最后",
            "依次",
            "先",
            "再",
            "接下来",
            "下一步",
        ]
        has_sequential_dependency = any(keyword in query for keyword in sequential_keywords)
        for idx, subtask_desc in enumerate(segments):
            task_id = f"task_{idx + 1}"
            ids.append(task_id)
            depends_on = [f"task_{idx}"] if has_sequential_dependency and idx > 0 else []
            subtasks.append(
                SubTask(
                    task_id=task_id,
                    description=subtask_desc,
                    priority=len(segments) - idx,
                    depends_on=depends_on,
                )
            )
            # dependencies = {
            #     "task_1": [],
            #     "task_2": ["task_1"],
            #     "task_3": ["task_2"]
            # }
            dependencies[task_id] = depends_on
            # parallel_groups = [["task_1", "task_2", "task_3"]] or [["task_1"], ["task_2"],["task_3"]]]
        parallel_groups = [[task_id] for task_id in ids] if has_sequential_dependency else [ids]
        return TaskDecomposition(
            subtasks=subtasks,
            parallel_groups=parallel_groups,
            dependencies=dependencies,
            strategy="parallel_first" if not has_sequential_dependency else "dependency_aware",
        )

    async def _aexecute_decomposed_tasks(
        self,
        req_query: str,
        request_id: str,
        decomposition: TaskDecomposition,
        system_prompt: str,
        available_tools: list[dict[str, Any]],
        skill_map: dict[str, SkillFunc],
        mcp_tool_map: dict[str, SkillFunc],
        history_scope: MemoryScope,
        tool_selection: ToolSelection | None = None,
        state: AgentState | None = None,
        parent_run: RunTree | None = None,
    ) -> ParallelTaskResult:
        started = time.perf_counter()
        tools_used: list[str] = []
        task_outputs: dict[str, str] = {}
        failed_task_ids: list[str] = []
        assigned_agent_lock = Lock()
        task_agent_map: dict[str, str] = {}

        async def arun_one_task(
            subtask: SubTask,
        ) -> tuple[str, bool, str, int, bool, str, TaskStatus]:
            logger.info("Executing subtask %s: %s", subtask.task_id, subtask.description)

            task_started = time.perf_counter()
            selected_agent = self.orchestrator.select_agent_by_subtask(subtask)
            subtask.assigned_agent_id = selected_agent.agent_id
            await self.memory.aupsert_task_state(
                request_id=request_id,
                task_id=subtask.task_id,
                scope=history_scope,
                description=subtask.description,
                status=TaskStatus.RUNNING,
                depends_on=subtask.depends_on,
                assigned_agent_id=subtask.assigned_agent_id,
            )
            if state is not None:
                with assigned_agent_lock:
                    if subtask.assigned_agent_id not in state.assigned_agent_ids:
                        state.assigned_agent_ids.append(subtask.assigned_agent_id)

            async def _execute_subtask_core(query: str, agent_id: str) -> AgentTaskExecutionResult:
                request = AgentTaskExecutionRequest(
                    task_id=subtask.task_id,
                    query=query,
                    agent_id=agent_id,
                    preferred_tool=subtask.preferred_tool,
                    context={},
                )
                return await self.orchestrator.aexecute_with_callback_agent(
                    request,
                    callback_execute=lambda q, aid: self._aexecute_agent(
                        q,
                        request_id,
                        aid,
                        system_prompt,
                        available_tools,
                        skill_map,
                        mcp_tool_map,
                        tool_selection,
                        parent_run,
                    ),
                )

            # 1. 尝试首次正常执行
            initial_error = "agent_execution_failed"
            try:
                first_res = await _execute_subtask_core(
                    subtask.description, subtask.assigned_agent_id
                )
                if first_res.success and first_res.output:
                    return (
                        subtask.task_id,
                        True,
                        first_res.output,
                        0,
                        False,
                        subtask.assigned_agent_id,
                        TaskStatus.SUCCEEDED,
                    )
                initial_error = first_res.output or "agent_execution_failed"
            except Exception as e:  # noqa: BLE001
                initial_error = str(e)

            async def _retry_handler(ctx: FaultContext, attempt_no: int) -> RecoveryAttempt:
                diag = self.fault_analyzer.analyze(ctx)
                backoff = retry_backoff_seconds(diag)
                if backoff > 0:
                    await asyncio.sleep(backoff)
                res = await _execute_subtask_core(subtask.description, subtask.assigned_agent_id)
                return RecoveryAttempt(success=res.success and bool(res.output), output=res.output)

            async def _fallback_handler(ctx: FaultContext, diag: Any) -> RecoveryAttempt:
                if not (tool_selection and tool_selection.fallback_tools):
                    return RecoveryAttempt(success=False, output="")
                for fallback_tool in tool_selection.fallback_tools:
                    if fallback_tool in skill_map:
                        try:
                            res = await self._acall_tool_with_query(
                                skill_map[fallback_tool], subtask.description
                            )
                            summary = self.tool_result_to_string(res)
                            return RecoveryAttempt(success=True, output=summary)
                        except Exception as e:  # noqa: BLE001
                            return RecoveryAttempt(success=False, output=str(e))
                return RecoveryAttempt(success=False, output="")

            async def _rag_only_handler(ctx: FaultContext, diag: Any) -> RecoveryAttempt:
                try:
                    rag_docs = await asyncio.to_thread(self.retriever.retrieve, subtask.description)
                    summary = "\n".join([str(doc) for doc in rag_docs])
                    return RecoveryAttempt(bool(summary), output=summary)
                except Exception as e:  # noqa: BLE001
                    return RecoveryAttempt(success=False, output=str(e))

            ctx = FaultContext(
                request_id=request_id,
                task_id=subtask.task_id,
                agent_id=subtask.assigned_agent_id,
                tool_name=subtask.preferred_tool or "unknown",
                error_message=initial_error,
                error_type="SubtaskExecutionError",
                retry_count=0,
                elapsed_time_ms=(time.perf_counter() - task_started) * 1000,
            )
            # 启动时创建一次
            async with AsyncPostgresSaver.from_conn_string(
                settings.langgraph_checkpoint_dsn
            ) as checkpointer:
                await checkpointer.setup()
                workflow = FaultRecoveryWorkflow(
                    analyzer=self.fault_analyzer,
                    executor=FaultExecutor(_retry_handler, _fallback_handler, _rag_only_handler),
                    max_retry_attempts=self.config_manager.get_task_max_retries(),
                    checkpointer=checkpointer,
                )
            # 4. 执行图，处理 HITL 挂起与完成
            subtask_thread_id = f"{request_id}_{subtask.task_id}"
            outcome = await workflow.run(ctx, thread_id=subtask_thread_id)
            if outcome.status == "awaiting_human":
                # 若停留在 L4，触发人工干预请求；恢复时传入用户的 decision ("retry" / "skip" / "abort")

                diag_info = outcome.human_question or {}
                root_cause = diag_info.get("root_cause", initial_error)
                logger.warning(
                    "[%s] Subtask %s entered L4 HITL, root_cause: %s",
                    request_id,
                    subtask.task_id,
                    root_cause,
                )
                # 2. 登记到人工干预处理器（待后台人工跟进）
                self._intervention_handler.create_intervention_request(
                    task_id=subtask.task_id,
                    reason=root_cause,
                    user_id=state.active_agent_id if state else "system",
                )
                self.alert_manager.create_alert(
                    alert_type=AlertTypes.FAULT_ALERT,
                    severity=AlertSeverity.HIGH,
                    title=f"子任务 {subtask.task_id} 需要人工干预",
                    message=f"错误根因: {root_cause}",
                    affected_resource=subtask.task_id,
                    context={
                        "thread_id": subtask_thread_id,
                        "suggestions": diag_info.get("suggestions", []),
                        "options": diag_info.get("options", ["retry", "skip", "abort"]),
                    },
                )

                # 此处可对接 self._intervention_handler 挂起并等待人工输入，示例默认 skip
                # outcome = await workflow.run(ctx, thread_id=subtask_thread_id, resume="skip")
                await self.memory.aupsert_task_state(
                    request_id=request_id,
                    task_id=subtask.task_id,
                    scope=history_scope,
                    description=subtask.description,
                    status=TaskStatus.WAITING_HUMAN,
                    assigned_agent_id=subtask.assigned_agent_id,
                    depends_on=subtask.depends_on,
                    error_message=str(root_cause),
                    retry_count=outcome.attempts,
                )
                return (
                    subtask.task_id,
                    False,
                    "任务等待人工决策。",
                    outcome.attempts,
                    True,
                    subtask.assigned_agent_id,
                    TaskStatus.WAITING_HUMAN,
                )

            return (
                subtask.task_id,
                outcome.resolved,
                outcome.output or initial_error,
                outcome.attempts,
                outcome.level >= 2,
                subtask.assigned_agent_id,
                TaskStatus.SUCCEEDED if outcome.resolved else TaskStatus.FAILED,
            )

        task_status_map = {subtask.task_id: "queued" for subtask in decomposition.subtasks}
        for subtask in decomposition.subtasks:
            await self.memory.aupsert_task_state(
                request_id=request_id,
                task_id=subtask.task_id,
                scope=history_scope,
                description=subtask.description,
                status=TaskStatus.QUEUED,
                depends_on=subtask.depends_on,
            )
        batches: list[list[SubTask]] = self._build_dependency_batches(decomposition)

        for batch in batches:
            runnable_subtasks: list[SubTask] = []
            for subtask in batch:
                deps = decomposition.dependencies.get(subtask.task_id, subtask.depends_on)
                failed_deps = [dep for dep in deps if task_status_map.get(dep) != "success"]
                if failed_deps:
                    logger.warning(
                        "Subtask %s skipped due to failed dependencies: %s",
                        subtask.task_id,
                        failed_deps,
                    )
                    task_status_map[subtask.task_id] = "skipped"
                    failed_task_ids.append(subtask.task_id)
                    await self.memory.aupsert_task_state(
                        request_id=request_id,
                        task_id=subtask.task_id,
                        scope=history_scope,
                        description=subtask.description,
                        status=TaskStatus.SKIPPED,
                        assigned_agent_id=subtask.assigned_agent_id,
                        result="",
                        error_message="Skipped due to failed dependencies",
                        retry_count=0,
                        depends_on=subtask.depends_on,
                    )
                    continue
                runnable_subtasks.append(subtask)
            if not runnable_subtasks:
                logger.info("No runnable subtasks in this batch, moving to next batch")
                continue

            max_workers = min(
                max(len(decomposition.subtasks), 1), self.config_manager.get_task_max_workers()
            )
            semaphore = asyncio.Semaphore(max_workers)

            async def _limited(
                subtask: SubTask,
                _sem: asyncio.Semaphore = semaphore,
            ) -> tuple[str, bool, str, int, bool, str, TaskStatus]:
                async with _sem:
                    return await arun_one_task(subtask)

            for subtask in runnable_subtasks:
                task_status_map[subtask.task_id] = "running"

            results = await asyncio.gather(*(_limited(st, semaphore) for st in runnable_subtasks))
            for tid, success, out, retry_times, used_fallback, agent_id, status in results:
                task_agent_map[tid] = agent_id
                task_status_map[tid] = status.value
                if status is TaskStatus.WAITING_HUMAN:
                    continue
                await self.memory.aupsert_task_state(
                    request_id=request_id,
                    task_id=tid,
                    scope=history_scope,
                    description=next(
                        item.description for item in decomposition.subtasks if item.task_id == tid
                    ),
                    status=status,
                    assigned_agent_id=agent_id,
                    result=out if success else "",
                    error_message="" if success else out,
                    retry_count=retry_times,
                )

                if success:
                    task_outputs[tid] = out
                    logger.info(
                        "Subtask %s completed successfully (retries=%d, used_fallback=%s, assigned_agent=%s)",
                        tid,
                        retry_times,
                        used_fallback,
                        agent_id,
                    )
                else:
                    task_status_map[tid] = "failed"
                    failed_task_ids.append(tid)
                    logger.warning(
                        "Subtask %s failed after %d retries, last error: %s (assigned_agent=%s)",
                        tid,
                        retry_times,
                        out,
                        agent_id,
                    )
                if state is not None:
                    state.retry_count += retry_times
                    if used_fallback:
                        state.fallback_used = True
                        state.fallback_strategy = "tool_chain"

        total_tasks = len(decomposition.subtasks)
        completed_tasks = total_tasks - len(failed_task_ids)
        elapsed = time.perf_counter() - started
        return ParallelTaskResult(
            completed_tasks=completed_tasks,
            failed_tasks=len(failed_task_ids),
            total_tasks=total_tasks,
            total_time=elapsed,
            tools_used=sorted(set(tools_used)),
            task_outputs=task_outputs,
            failed_task_ids=failed_task_ids,
            task_agent_mapping=task_agent_map,
            task_status_mapping=task_status_map,
            execute_batches=[[subtask.task_id for subtask in batch] for batch in batches],
            skipped_task_ids=[
                task_id for task_id, status in task_status_map.items() if status == "skipped"
            ],
        )

    @staticmethod
    def _merge_multi_task_results(task_result: ParallelTaskResult) -> str:
        if not task_result.task_outputs:
            return "多任务执行未返回有效结果，请稍后重试。"
        lines: list[str] = []
        for task_id in sorted(task_result.task_outputs.keys()):
            lines.append(f"[{task_id}] {task_result.task_outputs[task_id]}")
        if task_result.failed_task_ids:
            lines.append(f"以下子任务失败: {', '.join(task_result.failed_task_ids)}")
        return "\n".join(lines)

    async def _areflect_multi_agent_answer(
        self,
        *,
        query: str,
        draft_answer: str,
        task_result: ParallelTaskResult,
        agentState: AgentState,
        parent_run: RunTree | None = None,
    ) -> str:
        evidence = json.dumps(
            task_result.task_outputs,
            ensure_ascii=False,
            sort_keys=True,
        )

        async def _review_handler(
            review_query: str,
            review_draft: str,
            review_evidence: str,
        ) -> ReviewDecision:
            if "reviewer_agent" not in agentState.assigned_agent_ids:
                agentState.assigned_agent_ids.append("reviewer_agent")
            prompt = (
                "你是独立审阅 Agent。检查回答的正确性、完整性、证据一致性和不确定性声明。"
                "\n不得补充证据中不存在的事实。"
                '\n只输出 JSON：{"approved": true, "score": 0.0, "feedback": "..."}'
                f"\n通过阈值：{settings.reflection_min_score}"
                f"\n\n子任务证据：\n{review_evidence}"
                f"\n\n待审阅回答：\n{review_draft}"
            )
            raw = await self.model_client.achat(
                review_query,
                prompt,
                tools=[],
                parent_run=parent_run,
                _token_counter=agentState.token_counter,
            )
            parsed = _safe_parse_intent_json(str(raw or "{}"))
            approve_row = parsed.get("approved", False)
            score = float(parsed.get("score", 0.0))  # type: ignore
            feedback = str(parsed.get("feedback", ""))
            return ReviewDecision(
                approved=bool(approve_row and score >= settings.reflection_min_score),
                score=score,
                feedback=feedback,
            )

        async def _revise_handler(
            revise_query: str,
            revise_draft: str,
            revise_evidence: str,
            feedback: str,
        ) -> str:
            if "reviser_agent" not in agentState.assigned_agent_ids:
                agentState.assigned_agent_ids.append("reviser_agent")
            prompt = (
                "你是修订 Agent。根据审阅意见修订答案。"
                "\n只能使用给定证据，不得编造数据，不得调用工具。"
                "\n直接输出修订后的最终答案。"
                f"\n\n子任务证据：\n{revise_evidence}"
                f"\n\n原回答：\n{revise_draft}"
                f"\n\n审阅意见：\n{feedback}"
            )
            revised = await self.model_client.achat(
                revise_query,
                prompt,
                tools=[],
                parent_run=parent_run,
                _token_counter=agentState.token_counter,
            )
            return str(revised or "").strip()

        workflow = ReflectionWorkflow(
            execute=ReflectionExecutor(_review_handler, _revise_handler),
            max_rounds=settings.reflection_max_rounds,
        )
        outcome = await workflow.run(
            query=query,
            draft=draft_answer,
            evidence=evidence,
        )
        agentState.reflection_rounds = outcome.rounds
        agentState.reflection_approved = outcome.approved
        agentState.reflection_trail = outcome.trail
        return outcome.answer

    @staticmethod
    async def _aapply_tool(tool_fn: SkillFunc, args: dict[str, Any]) -> Any:
        """执行工具：async 闭包 await，sync 工具直接调用。"""
        if asyncio.iscoroutinefunction(tool_fn):
            return await tool_fn(**args)
        return tool_fn(**args)

    @staticmethod
    async def _acall_tool_with_query(tool_fn: SkillFunc, query: str) -> dict[str, Any]:
        try:
            return cast(dict[str, Any], await ChatService._aapply_tool(tool_fn, {"query": query}))
        except TypeError:
            try:
                return cast(
                    dict[str, Any], await ChatService._aapply_tool(tool_fn, {"input": query})
                )
            except TypeError:
                return cast(
                    dict[str, Any], await ChatService._aapply_tool(tool_fn, {"text": query})
                )

    async def _aexecute_agent(
        self,
        query: str,
        request_id: str,
        agent_id: str,
        system_prompt: str,
        available_tools: list[dict[str, Any]],
        skill_map: dict[str, SkillFunc],
        mcp_tool_map: dict[str, SkillFunc],
        tool_selection: ToolSelection | None = None,
        parent_run: RunTree | None = None,
    ) -> str:
        local_state = AgentState(active_agent_id=agent_id)
        return await self._arun_agent_loop(
            query=query,
            request_id=request_id,
            system_prompt=system_prompt,
            available_tools=available_tools,
            skill_map=skill_map,
            mcp_tool_map=mcp_tool_map,
            state=local_state,
            tool_selection=tool_selection,
            parent_run=parent_run,
        )

    @staticmethod
    def _build_dependency_batches(decomposition: TaskDecomposition) -> list[list[SubTask]]:
        # 示例 1：串行模式 has_sequential_order=True，segments 3 条
        # plaintext
        # ids = ["task_1", "task_2", "task_3"]
        # dependencies = {
        #     "task_1": [],
        #     "task_2": ["task_1"],
        #     "task_3": ["task_2"]
        # }
        # parallel_groups = [["task_1"], ["task_2"], ["task_3"]]
        # strategy = "dependency_aware"
        # 执行流程：task1 完成 → 再执行 task2 → 再执行 task3
        # 示例 2：并行模式 has_sequential_order=False，segments 3 条
        # plaintext
        # ids = ["task_1", "task_2", "task_3"]
        # dependencies = {
        #     "task_1": [],
        #     "task_2": [],
        #     "task_3": []
        # }
        # parallel_groups = [["task_1", "task_2", "task_3"]]
        # strategy = "parallel_first"
        # 执行流程：三个任务同时并发运行，互不等待
        # [[A,B], [C], [D]]
        subtask_id_map: dict[str, SubTask] = {
            subtask.task_id: subtask for subtask in decomposition.subtasks
        }
        depend_map: dict[str, set[str]] = {
            subtask.task_id: set(
                decomposition.dependencies.get(subtask.task_id, subtask.depends_on)
            )
            for subtask in decomposition.subtasks
        }
        # 计算每个子任务的依赖数量
        batches: list[list[SubTask]] = []
        resolve: set[str] = set()
        while len(resolve) < len(subtask_id_map):
            # 找出所有依赖已解决的子任务
            ready_ids = [
                task_id
                for task_id, deps in depend_map.items()
                if task_id not in resolve and deps.issubset(resolve)
            ]
            if not ready_ids:
                unresolved = sorted(set(subtask_id_map.keys()) - resolve)
                raise ValueError(f"Task dependency cycle detected among: {unresolved}")
            batch = [subtask_id_map[task_id] for task_id in ready_ids]
            batches.append(batch)
            resolve.update(ready_ids)
        return batches

    @staticmethod
    def _fit_agent_prompt(system_prompt: str) -> str:
        """限制 Agent 多轮工具结果累积后的系统提示词大小。"""
        return trim_text(
            system_prompt,
            settings.llm_input_token_budget,
            keep="both",
        )

    @staticmethod
    def _fit_tool_result(tool_result: str) -> str:
        """限制单次工具结果占用的上下文预算。"""
        return trim_text(
            tool_result,
            settings.context_tool_result_token_budget,
            keep="head",
        )

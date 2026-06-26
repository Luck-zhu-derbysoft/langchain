from dataclasses import dataclass, field
import json
import logging
import re
import uuid
from typing import Any, Callable, Dict, TypedDict, cast

from langsmith.run_trees import RunTree

from app.config.settings import settings
from app.infrastructure.agent.a2a_protocol import IntentClassification
from app.infrastructure.llm.model_client import ModelClient,BudgetExceededError
from app.infrastructure.mcp.mcp_tool import get_tool_map, get_tools_metadata
from app.infrastructure.memory.redis_postgres_conversation_memory import (
    MemoryScope,
    RedisPostgresConversationMemoryStore,
)
from app.infrastructure.skill.db.mysql_skill import MYSQL_TOOL_USER_PROMPT
from app.observability.langsmith_tracer import LangSmithTracer
from app.rag.retrieval.retriever import Retriever
from app.schemas.chat import ChatRequest, ChatResponse, Citation
from app.infrastructure.skill.date.time_skill import TIME_TOOLS_METADATA as time_meta, TIME_SKILL_MAP
from app.tool.static import _safe_parse_intent_json, _to_bool



logger = logging.getLogger(__name__)

SkillFunc = Callable[..., Dict[str, Any]]

@dataclass
class AgentState:
    answer: str = ""
    current_turn_count: int = 0
    token_counter: list[int] = field(default_factory=lambda: [0])
    previous_tool_calls: set[str] = field(default_factory=set)
    read_only_mode: bool =False
    tool_error_count: int = 0

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
    ) -> None:
        # 新增：用于显式追踪
        self.model_client = model_client
        self.retriever = retriever
        self.trace = trace
        self.memory = memory
    def ask(self, req: ChatRequest, *, parent_run: RunTree | None = None) -> ChatResponse:
        ask_run = self.trace.start_child(
            parent_run=parent_run,
            name="service.chat.ask",
            run_type="chain",
            inputs={"query": req.query, "business_context": req.business_context},
            tags=["service", "chat"],
        )
        request_id = str(uuid.uuid4())
        trace_id= request_id
        logger.info(
            "[%s] Chat ask start: tenant=%s user=%s thread=%s query=%s",
            request_id, req.tenant_id, req.user_id, req.thread_id, str(req.query)[:200],
        )
        intent_classification,module_activation = self._classify_intent(req.query, self.model_client, parent_run=ask_run)
        try:
            # 节点1：意图分类
            # 节点2：工具解析
            available_tools, SKILL_MAP, mcp_tool_map = self._resolve_tools(module_activation) # type: ignore
            logger.info(
                "[%s] Tools resolved: total=%d skills=%d mcp=%d",
                request_id, len(available_tools), len(SKILL_MAP), len(mcp_tool_map),
            )

            #读取记忆内容
            # 节点3：加载历史记忆
            history_memory_params = MemoryScope(tenant_id=req.tenant_id , user_id=req.user_id, thread_id=req.thread_id)
            memory_context = self.memory.load_context(history_memory_params,max_turns=5)
            history_prompt_text = memory_context.as_prompt_text()
            current_turn_count = memory_context.turn_count
            logger.info("[%s] Memory loaded: turn_count=%d", request_id, current_turn_count)
            context = ""
            citations: list[Citation] = []
            # 节点4：RAG 检索
            if  self.is_query_time(req.query) or not module_activation.get("rag", True):
                logger.info("[%s] RAG retrieval skipped (time query or rag disabled)", request_id)
                docs = []
            else:
                docs = self.retriever.retrieve(req.query,
                                            top_k=settings.retrieval_final_k,
                                            history_text=history_prompt_text,
                                            where=None,  # 后续可替换成租户/分类过滤
                                            parent_run=ask_run)
            # 继续使用原始检索结果，不抛出异常
                docs = docs[: settings.context_top_k]

                context_lines: list[str] = []

                for doc in docs:
                    context_lines.append(f"[{doc['source_id']}] {doc['content']}")
                    citations.append(Citation(source_id=doc["source_id"], snippet=doc["content"]))

                context = "\n".join(context_lines)
                logger.info("[%s] RAG retrieved %d docs (context_top_k applied)", request_id, len(docs))
            # 3. 智能体 ReAct 推理循环
            # 节点5：智能体 ReAct 推理循环
            answer = ""
            state = AgentState()
            system_prompt = self._build_base_system_prompt(context, history_prompt_text, req.query, mcp_tool_map)
            logger.info("[%s] Agent loop start", request_id)
            answer = self._run_agent_loop(req.query,system_prompt,available_tools, SKILL_MAP, mcp_tool_map, state, parent_run=ask_run)
            tool_error_count = state.tool_error_count
            logger.info(
                "[%s] Agent loop finished: answer_len=%d tool_error_count=%d",
                request_id, len(answer or ""), tool_error_count,
            )

            if not answer and "工具调用结果 (" in system_prompt:
                summary_prompt = (
                    system_prompt
                    + "\n\n请基于上面的知识库和工具结果，输出最终结论。"
                    + "\n要求："
                    + "\n1) 给出明确结论；"
                    + "\n2) 如果工具失败或数据不足，明确说明不确定性；"
                    + "\n3) 不要再调用任何工具。"
                )
                logger.info("Agent entering summary phase to generate final answer")
                final_answer = self.model_client.chat(
                    user_query=req.query,
                    system_prompt=summary_prompt,
                    tools=[],
                    return_message=False,
                    parent_run=ask_run,
                    _token_counter=state.token_counter,  # ← 新增：传入 token 计数器
                )
                answer = str(final_answer or "").strip()
            if not answer:
                if tool_error_count > 0:
                    answer = "部分数据工具调用失败，以下结论基于当前知识库与可用结果生成，建议稍后重试数据库查询。"
                else:
                    answer = "未能在限定轮次内生成最终答案。"
            #补充当前返回结果到记忆体中 时间数据不需要回写
            # 节点6：回写记忆
            skip_memory_write = settings.time_query_skip_memory_write and self.is_query_time(req.query)
            if skip_memory_write:
                logger.info("[%s] Memory write-back skipped (time query)", request_id)
            if  not skip_memory_write:
                # 1) 写入用户提问
                self.memory.append_turn(history_memory_params,
                                        role="user",
                                        content=req.query,
                                        metadata={"request_id": request_id,
                                                   "current_turn_count": current_turn_count+1,})
                # 2) 写入工具调用结果作为系统消息
                self.memory.append_turn(history_memory_params,
                                        role="assistant",
                                        content=answer,
                                        metadata={"request_id":request_id,
                                                  "citations_count": len(citations),})
                # 3) 达到阈值时更新长期摘要
                after_turn_count = current_turn_count + 2
                logger.info("[%s] Memory write-back done: after_turn_count=%d", request_id, after_turn_count)
                if after_turn_count >= settings.memory_summary_update_turn_threshold:
                    logger.info("[%s] Updating long-term memory summary", request_id)
                    summary_text = self.build_memory_summary(history_prompt_text=history_prompt_text,
                                                user_query=req.query,
                                                answer=answer,
                                                parent_run=ask_run)

                    self.memory.save_summary(history_memory_params, summary_text)

            resp = ChatResponse(
                answer=answer,
                citations=citations,
                model=settings.model_name,
                request_id=request_id,
                intent=intent_classification.intent,
                intent_confidence=intent_classification.confidence,
                trace_id=trace_id,
            )
            # 新增：在响应中添加追踪信息
            self.trace.end_run(
                ask_run,
                outputs={
                    "request_id": request_id,
                    "retrieved_docs": len(docs),
                    "model": settings.model_name,
                },
            )
            return resp
        except BudgetExceededError as e:
                logger.warning("[%s] Budget exceeded: %s", request_id, e)
                self.trace.end_run(ask_run, error=LangSmithTracer.format_error(e))
                return ChatResponse(
                    answer=str(e),
                    citations=[],
                    model=settings.model_name,
                    request_id=request_id,
                    intent=intent_classification.intent,
                    intent_confidence=intent_classification.confidence,
                    trace_id=trace_id,
                )

        except Exception as e:
            # 在异常情况下也要结束追踪
            logger.exception("[%s] Chat ask failed: %s", request_id, e)
            self.trace.end_run(ask_run, error=LangSmithTracer.format_error(e))
            raise

    @staticmethod
    def tool_result_to_string(tool_result: Dict[str, Any]) -> str:
        status = tool_result.get("status", "error")
        message = tool_result.get("message", "")
        error_code = tool_result.get("error_code", "")
        data_list = tool_result.get("data", [])
        if not isinstance(data_list, list):
            data_list = []

        row_count_raw = tool_result.get("row_count",tool_result.get("count",len(data_list)))
        try :
            row_count = int(row_count_raw)
        except Exception:
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
                formatted_rows.append(f"- {str(row)}")
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
        "今天", "日期", "几号", "星期", "几点", "当前时间",
        "today", "date", "day", "time", "weekday"
         ]
        return any(keyword in text for keyword in time_keywords)

    def build_memory_summary(self, history_prompt_text: str,
                             user_query:str,answer:str,parent_run:RunTree | None = None,) -> str:
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
        logger.debug("Building memory summary, prompt length=%d", len(summary_prompt))
        summary = self.model_client.chat(
            user_query="请生成长期记忆摘要",
            system_prompt=summary_prompt,
            tools=[],
            return_message=False,
            parent_run=parent_run,
            _token_counter=None,  # 摘要生成不计入主对话的 token 统计
        )
        return str(summary or "").strip()

    @staticmethod
    def _classify_intent(query: str, model_client: ModelClient, parent_run: RunTree | None = None) -> tuple[IntentClassification, dict[str, bool]]:
        """
        分类用户意图，返回详细的意图信息 + 模块激活决策
        Returns:
            (IntentClassification, {"mcp": bool, "rag": bool})
        """
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
            result = model_client.chat(
                user_query=query,
                system_prompt=classify_prompt,
                tools=[],
                return_message=False,
                parent_run=parent_run,
                _token_counter=None,
            )
            parsed = _safe_parse_intent_json(str(result or "{}"))
            intent_classification = IntentClassification(
                intent=parsed.get("intent", "unknown"), # type: ignore
                confidence=float(parsed.get("confidence", 0.0)), # type: ignore
                category=parsed.get("category", "other"),# type: ignore
                entities=parsed.get("entities", {}),# type: ignore
                reasoning=parsed.get("reasoning", ""),# type: ignore
            )
            # 保持原有的模块激活决策
            module_activation = {
                "mcp": _to_bool(parsed.get("need_db", True)),
                "rag": _to_bool(parsed.get("need_rag", True)),
            }
            logger.info("Intent classified: intent=%s confidence=%.2f category=%s mcp=%s rag=%s",
                        intent_classification.intent,
                        intent_classification.confidence,
                        intent_classification.category,
                        module_activation["mcp"],
                        module_activation["rag"])
            return intent_classification, module_activation

        except Exception as e:
            logger.warning("Intent classification failed, falling back to all modules: %s", e)
            return IntentClassification(
                intent="unknown",
                confidence=0.0,
                category="other",
                entities={},
                reasoning="Intent classification failed",
            ), {"mcp": True, "rag": True}  # 失败兜底：全部激活


    def _resolve_tools(self,intent: dict[str, Any]) -> ToolsResolution:
        available_tools:list[dict[str, Any]] = list(time_meta)
        mcp_tool_map: dict[str, SkillFunc] = {}
        mcp_tool_metadata: list = []
        if settings.mcp_enabled and intent.get("mcp", True):
            mcp_tool_map = get_tool_map()
            mcp_tool_metadata = get_tools_metadata()  # 确保 MCP 工具元数据已加载
            available_tools.extend(mcp_tool_metadata)
        skill_map = {**TIME_SKILL_MAP, **mcp_tool_map}  # 最终可用工具映射
        return ToolsResolution(
                    available=available_tools,
                    skill_map=skill_map,
                    mcp_map=mcp_tool_map,
                )


    def _build_base_system_prompt(self, context: str, history_prompt_text: str, query: str, mcp_tool_map: dict[str, SkillFunc]) -> str:
        system_prompt = (
            "你是一个企业级智能体。\n"
            "请结合本地知识库指导，自主决定是否需要编写 SQL 语句查询本地数据库以获取最新的实时数据。\n"
            "如果不需要调用工具，直接给出结论。\n"
            f"知识库背景:\n{context}"
        )
        if "query_mysql_database" in mcp_tool_map:
            system_prompt += (
                f"\nMySQL 工具使用规则:\n{MYSQL_TOOL_USER_PROMPT}"
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

    def _run_agent_loop(
            self,
            query: str,
            system_prompt: str,
            available_tools: list[dict[str, Any]],
            skill_map: dict[str, SkillFunc],
            mcp_tool_map: dict[str, SkillFunc],
            state: AgentState,
            parent_run: RunTree | None = None) -> str:
        answer = ""
        for iteration in range(settings.agent_max_iterations):
            logger.info(
                "Agent iteration %d/%d (read_only=%s)",
                iteration + 1, settings.agent_max_iterations, state.read_only_mode,
            )
            response_message = self.model_client.chat(
                user_query=query,
                system_prompt=system_prompt,
                tools=available_tools if not state.read_only_mode else [],
                return_message=True,
                parent_run=parent_run,
                _token_counter=state.token_counter,
            )
            tool_calls = getattr(response_message, "tool_calls", [])
            if not tool_calls:
                logger.info("Agent finished, no tool calls")
                logger.debug("Agent answer preview: %s", str(response_message.content or "")[:200])
                answer = response_message.content or ""
                break  # 没有工具调用，认为智能体思考结束
            for tool_call in tool_calls:
                tool_name = tool_call.function.name if tool_call.function else ""
                tool_args = tool_call.function.arguments if tool_call.function else {}
                tool_signature = f"{tool_name}:{tool_args}"
                if tool_signature in state.previous_tool_calls:
                        error_msg = f"工具 '{tool_name}' 使用相同参数被重复调用，可能导致死循环。"
                        logger.warning("Repeated tool call detected: %s", tool_signature[:200])
                        break  # 直接跳出工具调用循环，进入总结阶段
                state.previous_tool_calls.add(tool_signature)

                logger.info("Agent calling tool: %s", tool_name)
                logger.debug("tool args: %s", str(tool_args)[:200])
                if tool_name not in skill_map:
                    state.tool_error_count+=1
                    error_msg = f"未知工具: {tool_name}，当前可调用工具: {list(skill_map.keys())}"
                    system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                    if state.tool_error_count >= settings.agent_tool_failure_threshold:
                        state.read_only_mode = True
                        logger.warning("Tool failure threshold reached, forcing summary phase")
                    break
                try:
                    actual_args = tool_args
                    if isinstance(tool_args, str):
                        actual_args = json.loads(tool_args)
                    if not isinstance(actual_args, dict):
                        raise ValueError("工具参数解析错误，应该是一个 JSON 对象。")
                    result = skill_map[tool_name](**actual_args)
                    tool_summary = self.tool_result_to_string(result)
                    system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{tool_summary}"
                    logger.debug("tool result: %s", tool_summary[:200])
                except json.JSONDecodeError:
                    state.tool_error_count += 1
                    error_msg = "工具调用失败：参数解析错误，不是合法的 JSON 字符串。"
                    system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                    logger.warning("%s", error_msg)
                    if state.tool_error_count >= settings.agent_tool_failure_threshold:
                        state.read_only_mode = True
                        logger.warning("Tool failure threshold reached, forcing summary phase")
                        break
                except Exception as e:
                    state.tool_error_count += 1
                    error_msg = f"工具调用失败: {e}"
                    system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                    logger.warning("%s", error_msg)
                    if state.tool_error_count >= settings.agent_tool_failure_threshold:
                        state.read_only_mode = True
                        logger.warning("Tool failure threshold reached, forcing summary phase")
                        break
        return answer







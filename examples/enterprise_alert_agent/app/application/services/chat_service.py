import json
import uuid
from typing import Any, Callable, Dict

from langsmith.run_trees import RunTree

from app.config.settings import settings
from app.infrastructure.llm.model_client import ModelClient,BudgetExceededError
from app.infrastructure.mcp.mcp_tool import init_mcp, get_tool_map, get_tools_metadata
from app.infrastructure.memory.redis_postgres_conversation_memory import (
    MemoryScope,
    RedisPostgresConversationMemoryStore,
)
from app.infrastructure.skill.db.mysql_skill import MYSQL_TOOL_USER_PROMPT
from app.observability.langsmith_tracer import LangSmithTracer
from app.rag.retrieval.retriever import Retriever
from app.schemas.chat import ChatRequest, ChatResponse, Citation
from app.infrastructure.skill.date.time_skill import TIME_TOOLS_METADATA as time_meta, get_current_datetime, TIME_SKILL_MAP

SkillFunc = Callable[..., Dict[str, Any]]
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
        try:
            if self.is_query_time(req.query):
                time_result = get_current_datetime()
                data = time_result.get("data", [])
                item = data[0] if data else {}
                answer = (
                "当前时间是："
                f"{item.get('date', '')} {item.get('time', '')} "
                f"（{item.get('weekday_cn', '')}，{item.get('timezone', '')}）"
                )
                resp = ChatResponse(
                answer=answer,
                citations=[],
                model=settings.model_name,
                request_id=request_id,
                )
                self.trace.end_run(
                ask_run,
                outputs={
                "request_id": request_id,
                "retrieved_docs": 0,
                "model": settings.model_name,
                "time_direct": True,
                "tool_iso": item.get("iso", ""),
                "tool_timestamp": item.get("timestamp", 0),
                },
                )
                return resp
            # 1. 装载数据库工具集
            init_mcp_ok  = init_mcp()  # 确保 MCP 客户端和工具适配器已初始化，工具映射已准备好
            if not init_mcp_ok:
                print("⚠️ MCP 客户端初始化失败或未启用，继续使用标准工具集...")
            mcp_tool_map = get_tool_map()
            mcp_tool_metadata = get_tools_metadata()  # 确保 MCP 工具元数据已加载
            available_tools = time_meta + mcp_tool_metadata
            # 2. 自动利用解包合并路由图，主程序的 if tool_name in SKILL_MAP 可以无缝使用！
            SKILL_MAP: dict[str, SkillFunc] = {**TIME_SKILL_MAP, **mcp_tool_map}
            print(f"🔧 [工具集加载] 标准工具: {len(TIME_SKILL_MAP)}, MCP工具: {len(mcp_tool_map)}")
            #读取记忆内容
            history_memory_params = MemoryScope(tenant_id=req.tenant_id , user_id=req.user_id, thread_id=req.thread_id)
            memory_context = self.memory.load_context(history_memory_params,max_turns=5)
            history_prompt_text = memory_context.as_prompt_text()
            current_turn_count = memory_context.turn_count

            if settings.time_query_skip_retrieval and self.is_query_time(req.query):
                print("⏰ [时间查询] 检测到时间相关查询，跳过检索直接调用时间工具...")
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
            citations: list[Citation] = []

            for doc in docs:
                context_lines.append(f"[{doc['source_id']}] {doc['content']}")
                citations.append(Citation(source_id=doc["source_id"], snippet=doc["content"]))

            context = "\n".join(context_lines)

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
            if settings.time_tool_enabled :
                system_prompt += (
                                    "\n\n【硬规则】"
                                    "\n- 对日期/时间相关问题，必须调用 get_current_datetime 工具。"
                                    "\n- 禁止根据历史记忆、知识库或猜测回答日期时间。"
                                )
            if history_prompt_text:
                system_prompt += f"\n\n历史对话记忆:\n{history_prompt_text}"

            print(f"🤖 [Agent 开始思考...] 初始查询: {req.query} 系统提示词: {system_prompt}")
            # 3. 智能体 ReAct 推理循环
            answer = ""
            tool_error_count = 0
            rag_only_mode = False
            _token_counter = [0]   # ← 新增：可变列表用于跨调用累计 token
            previous_tool_calls = []  # 记录已调用过的工具，避免重复调用同一工具导致死循环
            for iteration in range(settings.agent_max_iterations):
                response_message = self.model_client.chat(
                    user_query=req.query,
                    system_prompt=system_prompt,
                    tools=available_tools if not rag_only_mode else [],
                    return_message=True,
                    parent_run=ask_run,
                    _token_counter=_token_counter,  # ← 新增：传入 token 计数器
                )
                tool_calls = getattr(response_message, "tool_calls", [])
                if not tool_calls:
                    print(f"🤖 [Agent 思考结束] 最终回答: {response_message.content}")
                    answer = response_message.content or ""
                    break  # 没有工具调用，认为智能体思考结束
                for tool_call in tool_calls:
                    tool_name = tool_call.function.name if tool_call.function else ""
                    tool_args = tool_call.function.arguments if tool_call.function else {}
                    if tool_name in previous_tool_calls:
                        error_msg = f"工具 '{tool_name}' 已被调用过一次，重复调用可能导致死循环。"
                        break  # 直接跳出工具调用循环，进入总结阶段
                    previous_tool_calls.append(tool_name)

                    print(f"🤖 [Agent 决定调用工具] 工具: {tool_name} 参数: {tool_args}")
                    if tool_name not in SKILL_MAP:
                        tool_error_count+=1
                        error_msg = f"未知工具: {tool_name}，当前可调用工具: {list(SKILL_MAP.keys())}"
                        system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                        if tool_error_count >= settings.agent_tool_failure_threshold:
                            rag_only_mode = True
                            print("⚠️ 工具调用错误次数达到阈值，强制智能体进入总结阶段...")
                        continue
                    try:
                        actual_args = tool_args
                        if isinstance(tool_args, str):
                            actual_args = json.loads(tool_args)
                        if not isinstance(actual_args, dict):
                            raise ValueError("工具参数解析错误，应该是一个 JSON 对象。")
                        is_mcp_tool = tool_name in mcp_tool_map
                        if is_mcp_tool:
                            print(f"🔌 调用 MCP 工具适配器执行工具: {tool_name}")
                        else:
                            print(f"🔧 调用标准工具: {tool_name}")
                        skill_func = SKILL_MAP[tool_name]
                        tool_result = skill_func(**actual_args)
                        tool_summary = self.tool_result_to_string(tool_result)
                        system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{tool_summary}"
                        print(f"🛠️ [工具调用结果] {tool_summary}")
                    except json.JSONDecodeError:
                        tool_error_count += 1
                        error_msg = "工具调用失败：参数解析错误，不是合法的 JSON 字符串。"
                        system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                        print(f"⚠️ {error_msg}")
                        if tool_error_count >= settings.agent_tool_failure_threshold:
                            rag_only_mode = True
                            print("⚠️ 工具调用错误次数达到阈值，强制智能体进入总结阶段...")
                            break
                    except Exception as e:
                        tool_error_count += 1
                        error_msg = f"工具调用失败: {e}"
                        system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                        print(f"⚠️ {error_msg}")
                        if tool_error_count >= settings.agent_tool_failure_threshold:
                            rag_only_mode = True
                            print("⚠️ 工具调用错误次数达到阈值，强制智能体进入总结阶段...")
                            break

            if not answer and "工具调用结果 (" in system_prompt:
                summary_prompt = (
                    system_prompt
                    + "\n\n请基于上面的知识库和工具结果，输出最终结论。"
                    + "\n要求："
                    + "\n1) 给出明确结论；"
                    + "\n2) 如果工具失败或数据不足，明确说明不确定性；"
                    + "\n3) 不要再调用任何工具。"
                )
                print("🤖 [Agent 进入总结阶段] 生成最终结论...")
                final_answer = self.model_client.chat(
                    user_query=req.query,
                    system_prompt=summary_prompt,
                    tools=[],
                    return_message=False,
                    parent_run=ask_run,
                    _token_counter=_token_counter,  # ← 新增：传入 token 计数器
                )
                answer = str(final_answer or "").strip()
            if not answer:
                if tool_error_count > 0:
                    answer = "部分数据工具调用失败，以下结论基于当前知识库与可用结果生成，建议稍后重试数据库查询。"
                else:
                    answer = "未能在限定轮次内生成最终答案。"
            #补充当前返回结果到记忆体中 时间数据不需要回写
            skip_memory_write = settings.time_query_skip_memory_write and self.is_query_time(req.query)
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
                if after_turn_count >= settings.memory_summary_update_turn_threshold:
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
                self.trace.end_run(ask_run, error=LangSmithTracer.format_error(e))
                return ChatResponse(
                    answer=str(e),
                    citations=[],
                    model=settings.model_name,
                    request_id=request_id,
                )

        except Exception as e:
            # 在异常情况下也要结束追踪
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
        print(f"📝 [生成对话摘要] 提示词: {summary_prompt}")
        summary = self.model_client.chat(
            user_query="请生成长期记忆摘要",
            system_prompt=summary_prompt,
            tools=[],
            return_message=False,
            parent_run=parent_run,
            _token_counter=None,  # 摘要生成不计入主对话的 token 统计
        )
        return str(summary or "").strip()



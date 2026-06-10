import json
import time
import uuid
from typing import Any, Callable, Dict,cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import CheckpointMetadata, empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langsmith.run_trees import RunTree

from app.config.settings import settings
from app.infrastructure.llm.model_client import ModelClient
from app.observability.langsmith_tracer import LangSmithTracer
from app.rag.retrieval.retriever import Retriever
from app.schemas.chat import ChatRequest, ChatResponse, Citation
from app.skill.db.mysql_skill import DB_SKILL_MAP as mysql_map
from app.skill.db.mysql_skill import DB_TOOLS_METADATA as mysql_meta
from app.skill.date.time_skill import TIME_SKILL_MAP as time_map
from app.skill.date.time_skill import TIME_TOOLS_METADATA as time_meta

SkillFunc = Callable[..., Dict[str, Any]]
class ChatService:
    def __init__(
        self,
        model_client: ModelClient,
        retriever: Retriever,
        trace: LangSmithTracer,
        memory: InMemorySaver,
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

        try:
            request_id = str(uuid.uuid4())
            # 1. 装载数据库工具集
            available_tools = mysql_meta + time_meta
            # 2. 自动利用解包合并路由图，主程序的 if tool_name in SKILL_MAP 可以无缝使用！
            SKILL_MAP: dict[str, SkillFunc] = {**mysql_map, **time_map}
            #读取记忆内容
            user_memory= bool(req.thread_id)  # 请求中是否携带了 memory 字段，且不为 None/空
            thread_id = req.thread_id  # 如果请求中没有 thread_id，就用 request_id 作为 thread_id
            print(f"[memory] thread_id={thread_id}, enabled={user_memory}")
            memory_config : RunnableConfig = {
                "configurable":{
                    "thread_id": thread_id,
                    "checkpoint_ns":"chat_memory",
                    },
            }
            history_text=""
            if user_memory:
                checkpoint_tuple = self.memory.get_tuple(memory_config)
                if checkpoint_tuple:
                    history_text = str(checkpoint_tuple.metadata.get("memory", "")).strip()
                    print(f"📖 [记忆读取成功] thread_id: {thread_id} history: {history_text}")
                else:
                    print(f"📖 [记忆读取] 未找到对应的记忆，thread_id: {thread_id}")
            # 调用下游时透传 parent_run
            if settings.time_query_skip_retrieval and self.is_query_time(req.query):
                print("⏰ [时间查询] 检测到时间相关查询，跳过检索直接调用时间工具...")
                docs = []
            else:
                docs = self.retriever.retrieve(req.query,
                                            top_k=settings.retrieval_final_k,
                                            history_text=history_text,
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
            # system_prompt = (
            #     "你是企业预警助手。请基于给定上下文回答。"
            #     "如果上下文不足，请明确说明不确定。"
            #     f"\n\n上下文:\n{context}"
            # )
            #  answer = self.model_client.chat(req.query, system_prompt,parent_run=ask_run)

            system_prompt = (
                "你是一个拥有本地数据库操作权限的企业级智能体。\n"
                "请结合本地知识库指导，自主决定是否需要编写 SQL 语句查询本地数据库以获取最新的实时数据。\n"
                "如果不需要调用工具，直接给出结论。\n"
                f"知识库背景:\n{context}"
            )
            if settings.time_tool_enabled :
                system_prompt += (
                                    "\n\n【硬规则】"
                                    "\n- 对日期/时间相关问题，必须调用 get_current_datetime 工具。"
                                    "\n- 禁止根据历史记忆、知识库或猜测回答日期时间。"
                                )
            if history_text:
                history_prompt_memory = self.trim_memory_by_turns(history_text, max_turns=2)
                system_prompt += f"\n\n历史对话记忆:\n{history_prompt_memory}"

            print(f"🤖 [Agent 开始思考...] 初始查询: {req.query} 系统提示词: {system_prompt}")
            # 3. 智能体 ReAct 推理循环
            answer = ""
            tool_error_count = 0
            for iteration in range(settings.agent_max_iterations):
                response_message = self.model_client.chat(
                    user_query=req.query,
                    system_prompt=system_prompt,
                    tools=available_tools,
                    return_message=True,
                    parent_run=ask_run,
                )
                tool_calls = getattr(response_message, "tool_calls", [])
                if not tool_calls:
                    print(f"🤖 [Agent 思考结束] 最终回答: {response_message.content}")
                    answer = response_message.content or ""
                    break  # 没有工具调用，认为智能体思考结束
                for tool_call in tool_calls:
                    tool_name = tool_call.function.name if tool_call.function else ""
                    tool_args = tool_call.function.arguments if tool_call.function else {}
                    print(f"🤖 [Agent 决定调用工具] 工具: {tool_name} 参数: {tool_args}")
                    if tool_name not in SKILL_MAP:
                        tool_error_count+=1
                        system_prompt += f"\n\n工具调用结果 ({tool_name}):\n未知工具: {tool_name}"
                        continue
                    #
                    try:
                        actual_args = tool_args
                        if isinstance(tool_args, str):
                            actual_args = json.loads(tool_args)
                        if not isinstance(actual_args, dict):
                            raise ValueError("工具参数解析错误，应该是一个 JSON 对象。")
                        skill_func = SKILL_MAP[tool_name]
                        tool_result = skill_func(**actual_args)

                        # 将工具调用结果作为新的上下文信息添加到 system prompt 中，供下一轮思考使用
                        tool_summary = self.tool_result_to_string(tool_result)
                        system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{tool_summary}"
                        print(f"🛠️ [工具调用结果] {tool_summary}")
                        # tool_result的数据格式Dict[str, Any] {'status': 'success', 'count': 3, 'data': [{'name': 'alert_history'}, {'name': 'sqlite_sequence'}, {'name': 'cmdb_assets'}]}
                        # answer = self.tool_result_to_string(tool_result)
                    except json.JSONDecodeError:
                        tool_error_count += 1
                        error_msg = "工具调用失败：参数解析错误，不是合法的 JSON 字符串。"
                        system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                        print(f"⚠️ {error_msg}")
                    except Exception as e:
                        tool_error_count += 1
                        error_msg = f"工具调用失败: {e}"
                        system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                        print(f"⚠️ {error_msg}")
                    # else:
                    #     error_msg = f"未知工具: {tool_name}"
                    #     system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                    #     print(f"⚠️ {error_msg}")
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
                )
                answer = str(final_answer or "").strip()
            if not answer:
                if tool_error_count > 0:
                    answer = "部分数据工具调用失败，以下结论基于当前知识库与可用结果生成，建议稍后重试数据库查询。"
                else:
                    answer = "未能在限定轮次内生成最终答案。"
            #补充当前返回结果到记忆体中 时间数据不需要回写
            skip_memory_write = settings.time_query_skip_memory_write and self.is_query_time(req.query)
            if user_memory and not skip_memory_write:
                trim_answer = self.trim_answer_by_length(answer, max_length=200)
                new_turn = f"User: {req.query}\nAssistant: {trim_answer}"
                merged_memory = f"{history_text}\n{new_turn}".strip()
                merged_memory = self.trim_memory_by_turns(merged_memory, max_turns=5)
                checkpoint = empty_checkpoint()
                metadata = cast(CheckpointMetadata,
                                {
                                    "source": "input",
                                    "step": int(time.time()),
                                    "memory": merged_memory,
                                },
                            )
                self.memory.put(memory_config, checkpoint, metadata,{})

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


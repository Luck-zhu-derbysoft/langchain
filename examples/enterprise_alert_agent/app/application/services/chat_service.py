from typing import Any, Dict
import uuid

from langsmith.run_trees import RunTree
from flashrank import Ranker, RerankRequest
from app.config.settings import settings
from app.infrastructure.llm.model_client import ModelClient
from app.rag.retrieval.retriever import Retriever
from app.schemas.chat import ChatRequest, ChatResponse, Citation
from app.observability.langsmith_tracer import LangSmithTracer
from app.skill.db.sqllite_skills import DB_TOOLS_METADATA as sqlite_meta, DB_SKILL_MAP as sqlite_map
from app.skill.db.mysql_skill import DB_TOOLS_METADATA as mysql_meta, DB_SKILL_MAP as mysql_map
import json


class ChatService:
    def __init__(
        self,
        model_client: ModelClient,
        retriever: Retriever,
        trace: LangSmithTracer,
    ) -> None:
        # 新增：用于显式追踪
        self.model_client = model_client
        self.retriever = retriever
        self.trace = trace
        self._ranker: Ranker | None = None
        if settings.rerank_enabled:
            self._ranker = Ranker(model_name=settings.rerank_model)

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
            available_tools = sqlite_meta + mysql_meta
            # 2. 自动利用解包合并路由图，主程序的 if tool_name in DB_SKILL_MAP 可以无缝使用！
            DB_SKILL_MAP = {**sqlite_map, **mysql_map}

            # 调用下游时透传 parent_run
            docs = self.retriever.retrieve(req.query, top_k=2, parent_run=ask_run)
            # flashrank
            if self._ranker is not None and docs:
                rerank_run = self.trace.start_child(
                    parent_run=ask_run,
                    name="rag.rerank",
                    run_type="tool",
                    inputs={
                        "query": req.query,
                        "candidate_count": len(docs),
                        "model": settings.rerank_model,
                    },
                    tags=["rag", "rerank", "flashrank"],
                )
                try:
                    id_to_doc: dict[str, dict[str, str]] = {}
                    passages: list[dict[str, object]] = []
                    for idx, doc in enumerate(docs):
                        source_id = doc["source_id"]
                        id_to_doc[source_id] = doc
                        passages.append(
                            {
                                "id": source_id,
                                "text": doc["content"],
                            }
                        )
                    ranked = self._ranker.rerank(
                        RerankRequest(
                            query=req.query,
                            passages=passages,
                        )
                    )
                    reranked_docs: list[dict[str, str]] = []
                    used_ids: set[str] = set()

                    for item in ranked:
                        ranked_id = str(item.get("id", "")).strip()
                        if ranked_id and ranked_id in id_to_doc and ranked_id not in used_ids:
                            reranked_docs.append(id_to_doc[ranked_id])
                            used_ids.add(ranked_id)

                    # 补齐未命中的文档，保证稳健性
                    for pid in id_to_doc:
                        if pid not in used_ids:
                            reranked_docs.append(id_to_doc[pid])
                    docs = reranked_docs
                    self.trace.end_run(
                        rerank_run,
                        outputs={"reranked_count": len(docs), "status": "ok"},
                    )
                except Exception as e:
                    self.trace.end_run(
                        rerank_run,
                        outputs={"reranked_count": 0, "status": "fallback_to_retrieval_order"},
                        error=LangSmithTracer.format_error(e),
                    )
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
                "你是一个拥有本地数据库操作权限的企业级告警分析智能体。\n"
                "请结合本地知识库指导，自主决定是否需要编写 SQL 语句查询本地数据库以获取最新的实时数据。\n"
                "如果不需要调用工具，直接给出结论。\n"
                f"知识库背景:\n{context}"
            )

            print(f"🤖 [Agent 开始思考...] 初始查询: {req.query}")
            # 3. 智能体 ReAct 推理循环
            answer = ""
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
                    if tool_name in DB_SKILL_MAP:
                        skill_func = DB_SKILL_MAP[tool_name]
                        try:
                            actual_args = tool_args
                            if isinstance(tool_args, str):
                                actual_args = json.loads(tool_args)
                            tool_result = skill_func(**actual_args)
                            # 将工具调用结果作为新的上下文信息添加到 system prompt 中，供下一轮思考使用
                            system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{tool_result}"
                            print(f"🛠️ [工具调用结果] {tool_result}")
                            # tool_result的数据格式Dict[str, Any] {'status': 'success', 'count': 3, 'data': [{'name': 'alert_history'}, {'name': 'sqlite_sequence'}, {'name': 'cmdb_assets'}]}
                            answer = self.tool_result_to_string(tool_result)
                        except json.JSONDecodeError:
                            error_msg = f"工具调用失败：参数解析错误，不是合法的 JSON 字符串。"
                            system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                            print(f"⚠️ {error_msg}")
                        except Exception as e:
                            error_msg = f"工具调用失败: {e}"
                            system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                            print(f"⚠️ {error_msg}")
                    else:
                        error_msg = f"未知工具: {tool_name}"
                        system_prompt += f"\n\n工具调用结果 ({tool_name}):\n{error_msg}"
                        print(f"⚠️ {error_msg}")
            if not answer:
                answer = "未能在限定轮次内生成最终答案。"
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
        # 1. 检查状态是否成功
        if tool_result.get("status") != "success":
            return f"查询失败: {tool_result.get('message', '未知错误')}"
        data_list = tool_result.get("data", [])
        # 2. 如果数据为空的处理
        if not data_list:
            return "查询成功，但未找到匹配的数据。"
        # 3. 动态解析每一行的所有键值对
        formatted_rows = []
        for row in data_list:
            # 将当前行的各个字段拼接为 "key: value" 的形式，例如 "table_name: alert_history, row_count: 1"
            row_str = ", ".join([f"{key}: {value}" for key, value in row.items()])
            formatted_rows.append(f"- {row_str}")
        # 4. 用换行符连接成最终的字符串输出
        return "\n".join(formatted_rows)

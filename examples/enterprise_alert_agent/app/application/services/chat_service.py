import uuid

from langsmith import traceable

from app.config.settings import settings
from app.infrastructure.llm.model_client import ModelClient
from app.rag.retrieval.retriever import Retriever
from app.schemas.chat import ChatRequest, ChatResponse, Citation


class ChatService:
    def __init__(self, model_client: ModelClient, retriever: Retriever) -> None:
        self.model_client = model_client
        self.retriever = retriever

    @traceable
    def ask(self, req: ChatRequest) -> ChatResponse:
        request_id = str(uuid.uuid4())
        docs = self.retriever.retrieve(req.query, top_k=2)

        context_lines: list[str] = []
        citations: list[Citation] = []
        for doc in docs:
            context_lines.append(f"[{doc['source_id']}] {doc['content']}")
            citations.append(Citation(source_id=doc["source_id"], snippet=doc["content"]))

        context = "\n".join(context_lines)
        system_prompt = (
            "你是企业预警助手。请基于给定上下文回答。"
            "如果上下文不足，请明确说明不确定。"
            f"\n\n上下文:\n{context}"
        )
        answer = self.model_client.chat(req.query, system_prompt)

        return ChatResponse(
            answer=answer,
            citations=citations,
            model=settings.model_name,
            request_id=request_id,
        )

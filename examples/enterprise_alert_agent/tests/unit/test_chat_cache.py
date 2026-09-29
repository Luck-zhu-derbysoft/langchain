"""Cache only read-only chat requests, never tool mutations."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from app.application.services.chat_service import ChatService
from app.schemas.chat import ChatRequest


class TestChatCache(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.service = object.__new__(ChatService)
        self.service.trace = Mock()
        self.service.model_client = Mock()
        self.service.metrics_collector = Mock()
        self.service.metrics_collector.get_metrics.return_value = None
        self.service.memory = SimpleNamespace(
            aload_context=AsyncMock(
                return_value=SimpleNamespace(as_prompt_text=lambda: "", turn_count=0)
            ),
            aappend_turn=AsyncMock(),
        )
        self.service._aresolve_tools = AsyncMock(
            return_value={"available": [], "skill_map": {}, "mcp_map": {}}
        )
        self.service._aselect_tool = AsyncMock(return_value=None)
        self.service._select_agent = Mock(return_value=SimpleNamespace(agent_id="router_agent"))
        self.service._build_base_system_prompt = Mock(return_value="")
        self.service._decompose_task = Mock(return_value=SimpleNamespace(subtasks=[object()]))
        self.service._arun_agent_loop = AsyncMock(return_value="created")
        self.service.is_query_time = Mock(return_value=False)
        self.request = ChatRequest(query="create RFP", tenant_id="tenant", user_id="user")

    async def test_create_request_skips_cache_read_and_write(self) -> None:
        self.service._aclassify_intent = AsyncMock(
            return_value=(
                SimpleNamespace(intent="create_rfp", category="create", confidence=1.0),
                {"rag": False},
            )
        )
        with (
            patch("app.application.services.chat_service.multi_tier_cache.get") as get_cache,
            patch("app.application.services.chat_service.multi_tier_cache.set") as set_cache,
        ):
            events = [event async for event in self.service.aask_stream(self.request)]

        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["answer"], "created")
        get_cache.assert_not_called()
        set_cache.assert_not_called()

    async def test_query_request_can_return_cached_answer(self) -> None:
        self.service._aclassify_intent = AsyncMock(
            return_value=(
                SimpleNamespace(intent="lookup", category="query", confidence=1.0),
                {"rag": False},
            )
        )
        with (
            patch(
                "app.application.services.chat_service.multi_tier_cache.get",
                return_value={
                    "answer": "cached",
                    "citations": [],
                    "model": "test",
                    "request_id": "old",
                },
            ) as get_cache,
            patch("app.application.services.chat_service.multi_tier_cache.set") as set_cache,
        ):
            events = [event async for event in self.service.aask_stream(self.request)]

        self.assertEqual([event["type"] for event in events], ["done"])
        self.assertEqual(events[0]["answer"], "cached")
        get_cache.assert_called_once_with(self.request.query, self.request.tenant_id)
        set_cache.assert_not_called()

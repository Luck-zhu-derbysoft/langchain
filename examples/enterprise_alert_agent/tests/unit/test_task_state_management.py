from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.services.chat_service import ChatService
from app.infrastructure.agent.a2a_protocol import (
    AgentTaskExecutionResult,
    ManualInterventionRequest,
    ParallelTaskResult,
    SubTask,
    TaskDecomposition,
    ToolSelection,
)
from app.infrastructure.memory.models import AgentTaskState, TaskStatus


def test_task_status_enum() -> None:
    assert TaskStatus.QUEUED.value == "queued"
    assert TaskStatus.RUNNING.value == "running"
    assert TaskStatus.WAITING_HUMAN.value == "waiting_human"
    assert TaskStatus.SUCCEEDED.value == "succeeded"
    assert TaskStatus.FAILED.value == "failed"
    assert TaskStatus.SKIPPED.value == "skipped"


def test_manual_intervention_request_schema() -> None:
    req = ManualInterventionRequest(task_id="task-101", intervention_type="skip")
    assert req.task_id == "task-101"
    assert req.intervention_type == "skip"


def build_chat_service(memory: MagicMock | None = None) -> ChatService:
    return ChatService(
        model_client=MagicMock(),
        retriever=MagicMock(),
        trace=MagicMock(),
        memory=memory or MagicMock(),
        _intervention_handler=MagicMock(),
        _alert_manager=MagicMock(),
        _metrics_collector=MagicMock(),
        fault_checkpointer=MagicMock(),
    )


def test_build_task_execution_snapshot_is_replay_safe() -> None:
    subtask = SubTask(
        task_id="task-1",
        description="query alerts",
        preferred_tool="search_alerts",
        depends_on=["task-0"],
        priority=2,
        assigned_agent_id="rag_agent",
    )
    tool_selection = ToolSelection(
        tool_name="search_alerts",
        confidence=0.9,
        fallback_tools=["rag_search"],
        reasoning="best match",
        agent_id="rag_agent",
    )

    snapshot = ChatService._build_task_execution_snapshot(
        req_query="check alerts",
        subtask=subtask,
        system_prompt="system",
        available_tools=[{"name": "search_alerts"}],
        tool_selection=tool_selection,
    )

    assert snapshot["request_query"] == "check alerts"
    assert "req_query" not in snapshot
    assert snapshot["subtask"]["status"] == TaskStatus.QUEUED.value
    assert snapshot["tool_selection"]["tool_name"] == "search_alerts"


@pytest.mark.asyncio
async def test_aresume_task_invalid_decision() -> None:
    service = build_chat_service()
    with pytest.raises(ValueError, match="Invalid decision"):
        await service.aresume_task(
            request_id="req-1",
            task_id="task-1",
            decision="invalid_choice",
            tenant_id="tenant-1",
            user_id="user-1",
        )


@pytest.mark.asyncio
async def test_aresume_task_not_found() -> None:
    memory = MagicMock()
    memory.aget_task_state = AsyncMock(return_value=None)
    service = build_chat_service(memory)
    with pytest.raises(LookupError, match="Task not found"):
        await service.aresume_task(
            request_id="req-1",
            task_id="task-1",
            decision="skip",
            tenant_id="tenant-1",
            user_id="user-1",
        )


@pytest.mark.asyncio
async def test_aresume_task_skip_success() -> None:
    task = AgentTaskState(
        request_id="req-1",
        task_id="task-1",
        tenant_id="tenant-1",
        user_id="user-1",
        thread_id="thread-1",
        status=TaskStatus.WAITING_HUMAN.value,
        description="test subtask",
        assigned_agent_id="rag_agent",
    )
    memory = MagicMock()
    memory.aget_task_state = AsyncMock(return_value=task)
    memory.aupsert_task_state = AsyncMock()

    checkpointer = MagicMock()
    workflow_outcome = MagicMock()
    workflow_outcome.output = "任务 task-1 已由人工跳过。"
    workflow_outcome.attempts = 1
    workflow_outcome.trail = ["l4_human_intervention:skip"]

    service = build_chat_service(memory)
    service.fault_checkpointer = checkpointer

    with patch("app.application.services.chat_service.FaultRecoveryWorkflow") as mock_wf_cls:
        mock_wf_instance = MagicMock()
        mock_wf_instance.run = AsyncMock(return_value=workflow_outcome)
        mock_wf_cls.return_value = mock_wf_instance

        res = await service.aresume_task(
            request_id="req-1",
            task_id="task-1",
            decision="skip",
            tenant_id="tenant-1",
            user_id="user-1",
        )

        assert res["status"] == TaskStatus.SKIPPED.value
        assert "task-1" in res["output"]
        memory.aupsert_task_state.assert_awaited_once()
        assert memory.aupsert_task_state.await_args.kwargs["replayable"] is False


@pytest.mark.asyncio
async def test_areplay_task_requires_snapshot() -> None:
    service = build_chat_service()
    task = AgentTaskState(
        request_id="req-1",
        task_id="task-1",
        tenant_id="tenant-1",
        user_id="user-1",
        thread_id="thread-1",
        description="test subtask",
        execution_snapshot={},
    )

    with pytest.raises(ValueError, match="No subtask snapshot data available"):
        await service.areplay_task_from_snapshot(task)


@pytest.mark.asyncio
async def test_areplay_task_resolves_live_tools_and_uses_snapshot_query() -> None:
    service = build_chat_service()
    service._aresolve_tools = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "available": [{"name": "live_tool"}],
            "skill_map": {"live_tool": MagicMock()},
            "mcp_map": {"live_tool": MagicMock()},
        }
    )
    replay_result = ParallelTaskResult(
        completed_tasks=1,
        failed_tasks=0,
        total_tasks=1,
        total_time=0.01,
        task_outputs={"task-1": "ok"},
        task_status_mapping={"task-1": TaskStatus.SUCCEEDED.value},
    )
    service._aexecute_decomposed_tasks = AsyncMock(return_value=replay_result)  # type: ignore[method-assign]
    task = AgentTaskState(
        request_id="req-1",
        task_id="task-1",
        tenant_id="tenant-1",
        user_id="user-1",
        thread_id="thread-1",
        description="test subtask",
        execution_snapshot={
            "request_query": "original query",
            "subtask": {
                "task_id": "task-1",
                "description": "test subtask",
                "preferred_tool": "live_tool",
                "depends_on": [],
                "priority": 1,
                "assigned_agent_id": "rag_agent",
            },
            "system_prompt": "system",
            "available_tools": [],
        },
    )

    result = await service.areplay_task_from_snapshot(task)

    assert result["success"] is True
    service._aresolve_tools.assert_awaited_once_with({"rag": True, "mcp": True})
    replay_kwargs = service._aexecute_decomposed_tasks.await_args.kwargs
    assert replay_kwargs["req_query"] == "original query"
    assert "live_tool" in replay_kwargs["skill_map"]
    assert "live_tool" in replay_kwargs["mcp_tool_map"]


@pytest.mark.asyncio
async def test_waiting_human_task_disables_replay() -> None:
    memory = MagicMock()
    memory.aupsert_task_state = AsyncMock()
    service = build_chat_service(memory)

    selected_agent = MagicMock()
    selected_agent.agent_id = "rag_agent"
    service.orchestrator.select_agent_by_subtask = MagicMock(return_value=selected_agent)
    service.orchestrator.aexecute_with_callback_agent = AsyncMock(
        return_value=AgentTaskExecutionResult(
            task_id="task-1",
            agent_id="rag_agent",
            success=False,
            output="tool failed",
        )
    )
    workflow_outcome = MagicMock()
    workflow_outcome.status = "awaiting_human"
    workflow_outcome.human_question = {"root_cause": "needs approval"}
    workflow_outcome.attempts = 1

    with patch("app.application.services.chat_service.FaultRecoveryWorkflow") as mock_wf_cls:
        mock_wf_instance = MagicMock()
        mock_wf_instance.run = AsyncMock(return_value=workflow_outcome)
        mock_wf_cls.return_value = mock_wf_instance

        await service._aexecute_decomposed_tasks(
            req_query="original query",
            request_id="req-1",
            decomposition=TaskDecomposition(
                subtasks=[SubTask(task_id="task-1", description="test subtask")],
                parallel_groups=[["task-1"]],
                dependencies={"task-1": []},
            ),
            system_prompt="system",
            available_tools=[],
            skill_map={},
            mcp_tool_map={},
            history_scope=MagicMock(tenant_id="tenant-1", user_id="user-1", thread_id="thread-1"),
        )

    waiting_call = memory.aupsert_task_state.await_args_list[-1]
    assert waiting_call.kwargs["status"] is TaskStatus.WAITING_HUMAN
    assert waiting_call.kwargs["replayable"] is False

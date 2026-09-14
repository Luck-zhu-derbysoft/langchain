from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.services.chat_service import ChatService
from app.infrastructure.agent.a2a_protocol import ManualInterventionRequest
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


@pytest.mark.asyncio
async def test_aresume_task_invalid_decision() -> None:
    service = ChatService(
        model_client=MagicMock(),
        retriever=MagicMock(),
        trace=MagicMock(),
        memory=MagicMock(),
        _intervention_handler=MagicMock(),
        _alert_manager=MagicMock(),
        _metrics_collector=MagicMock(),
    )
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
    service = ChatService(
        model_client=MagicMock(),
        retriever=MagicMock(),
        trace=MagicMock(),
        memory=memory,
        _intervention_handler=MagicMock(),
        _alert_manager=MagicMock(),
        _metrics_collector=MagicMock(),
        fault_checkpointer=MagicMock(),
    )
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

    service = ChatService(
        model_client=MagicMock(),
        retriever=MagicMock(),
        trace=MagicMock(),
        memory=memory,
        _intervention_handler=MagicMock(),
        _alert_manager=MagicMock(),
        _metrics_collector=MagicMock(),
        fault_checkpointer=checkpointer,
    )

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

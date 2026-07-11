


from dataclasses import dataclass
import time
from typing     import Callable

from app.infrastructure.agent.a2a_protocol import AgentTaskExecutionRequest, AgentTaskExecutionResult, SubTask
from app.infrastructure.agent.agent_registry import AgentDescriptor, AgentRegistry


@dataclass
class AgentDispatchResult:
    task_id: str
    agent_id: str
    success: bool
    output: str
    retry_count: int =0
    user_fallback: bool = False

class MultiAgentOrchestrator:
    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry

    def  select_agent_by_subtask(self, subtask: SubTask) -> AgentDescriptor:
        if subtask.preferred_tool:
            agents = self.registry.find_by_tool(subtask.preferred_tool)
            for agent in agents:
                if self.registry.is_healthy(agent.agent_id):
                    return agent

        router_agent = sorted(
            self.registry.find_by_capability("intent_routing"),
            key=lambda a: a.priority,
            reverse=True
        )
        for agent in router_agent:
            if self.registry.is_healthy(agent.agent_id):
                return agent
        return AgentDescriptor(
            agent_id="default_agent",
            display_name="Default Agent",
            capabilities=["intent_routing"],
            supported_tools=[],
            priority=0,
        )

    def execute_with_callback_agent(self,
                                    request:AgentTaskExecutionRequest,
                                    callback_execute: Callable[[str,str],str],)-> AgentTaskExecutionResult:
        started = time.perf_counter()
        try:
            output = callback_execute(request.query, request.agent_id)
            latency_ms = (time.perf_counter() - started) * 1000
            self.registry.record_success(request.agent_id)
            return AgentTaskExecutionResult(
                task_id=request.task_id,
                agent_id=request.agent_id,
                success=True,
                output=output,
                latency_ms=latency_ms
            )
        except Exception as e:
            output = str(e)
            latency_ms = (time.perf_counter() - started) * 1000
            self.registry.record_failure(request.agent_id)
            return AgentTaskExecutionResult(
                task_id=request.task_id,
                agent_id=request.agent_id,
                success=False,
                output=output,
                error_type=type(e).__name__,
                latency_ms=latency_ms
            )



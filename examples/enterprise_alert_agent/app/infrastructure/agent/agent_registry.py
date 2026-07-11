

import time

from pydantic.dataclasses import dataclass

from app.infrastructure.agent.a2a_protocol import AgentHealthState


@dataclass
class AgentDescriptor:
    agent_id: str
    display_name: str
    capabilities: list[str]
    supported_tools: list[str]
    priority: int = 0

class AgentRegistry:
    def __init__(self)-> None:
        self._agents: dict[str, AgentDescriptor] = {}
        self._health: dict[str, AgentHealthState] = {}

    def register_agent(self, agent: AgentDescriptor)-> None:
        self._agents[agent.agent_id] = agent

    def get_agent(self, agent_id: str) -> AgentDescriptor | None:
        return self._agents.get(agent_id)

    def list_agents(self) -> list[AgentDescriptor]:
        return sorted(self._agents.values(), key=lambda item: item.priority, reverse=True)

    def find_by_tool(self, tool_name: str) -> list[AgentDescriptor]:
        return [agent for agent in self._agents.values() if tool_name in agent.supported_tools]

    def find_by_capability(self, capability: str) -> list[AgentDescriptor]:
        return [
            agent
            for agent in self.list_agents()
            if capability in agent.capabilities
        ]
    def record_failure(self, agent_id: str, threshold: int = 3) -> bool:
        """记录失败，返回是否触发熔断"""
        state = self._health.setdefault(agent_id, AgentHealthState(agent_id=agent_id))
        state.consecutive_failures += 1
        if not state.is_open and state.consecutive_failures >= threshold:
            state.is_open = True
            state.open_at = time.perf_counter()
            return True
        return False
    def record_success(self, agent_id: str) -> None:
        if agent_id in self._health:
            self._health[agent_id].consecutive_failures = 0
            self._health[agent_id].is_open = False
    def is_healthy(self, agent_id: str, recovery_seconds: float = 30.0) -> bool:
        state = self._health.get(agent_id)
        if state is None or not state.is_open:
            return True
        if time.perf_counter() - state.open_at >= recovery_seconds:
            state.is_open = False
            state.consecutive_failures = 0
            return True
        return False

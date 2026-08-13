import time
from copy import deepcopy
from threading import RLock

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
    def __init__(self) -> None:
        self._agents: dict[str, AgentDescriptor] = {}
        self._health: dict[str, AgentHealthState] = {}
        self._lock = RLock()

    def register_agent(self, agent: AgentDescriptor) -> None:
        with self._lock:
            self._agents[agent.agent_id] = deepcopy(agent)

    def get_agent(self, agent_id: str) -> AgentDescriptor | None:
        with self._lock:
            agent = self._agents.get(agent_id)
            return deepcopy(agent) if agent else None

    def list_agents(self) -> list[AgentDescriptor]:
        with self._lock:
            agents = sorted(self._agents.values(), key=lambda item: item.priority, reverse=True)
            return deepcopy(agents)

    def find_by_tool(self, tool_name: str) -> list[AgentDescriptor]:
        with self._lock:
            agents = [
                agent for agent in self._agents.values() if tool_name in agent.supported_tools
            ]
            return deepcopy(agents)

    def find_by_capability(self, capability: str) -> list[AgentDescriptor]:
        with self._lock:
            agents = sorted(self._agents.values(), key=lambda item: item.priority, reverse=True)
            return deepcopy([agent for agent in agents if capability in agent.capabilities])

    def record_failure(self, agent_id: str, threshold: int = 3) -> bool:
        """记录失败，返回是否触发熔断"""
        with self._lock:
            state = self._health.setdefault(agent_id, AgentHealthState(agent_id=agent_id))
            state.consecutive_failures += 1
            if not state.is_open and state.consecutive_failures >= threshold:
                state.is_open = True
                state.opened_at = time.perf_counter()
                return True
            return False

    def record_success(self, agent_id: str) -> None:
        with self._lock:
            if agent_id in self._health:
                self._health[agent_id].consecutive_failures = 0
                self._health[agent_id].is_open = False

    def is_healthy(self, agent_id: str, recovery_seconds: float = 30.0) -> bool:
        with self._lock:
            state = self._health.get(agent_id)
            if state is None or not state.is_open:
                return True
            if time.perf_counter() - state.opened_at >= recovery_seconds:
                state.is_open = False
                state.consecutive_failures = 0
                return True
            return False

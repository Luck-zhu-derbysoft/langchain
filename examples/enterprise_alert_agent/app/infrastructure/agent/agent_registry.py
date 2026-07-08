

from pydantic.dataclasses import dataclass


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

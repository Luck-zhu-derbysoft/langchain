import sys
from pathlib import Path
from langchain.agents import create_agent,AgentState
from langgraph.checkpoint.memory import InMemorySaver
from langchain_openai import ChatOpenAI
from pydantic import SecretStr,BaseModel
from langchain_core.messages import HumanMessage,AnyMessage
from typing import TypedDict, Any,cast
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from langchain.tools import tool, ToolRuntime
from langchain.messages import ToolMessage

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # examples/enterprise_alert_agent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import settings


def get_user_info() -> str:
    """Look up information about the current user."""
    return "No user profile on file."

class CustomState(AgentState):
    user_name: str

class CustomContext(BaseModel):
    user_id: str

@tool
def update_user_info(
    runtime: ToolRuntime[CustomContext, CustomState],
) -> Command:
    """Look up and update user info."""
    user_id = runtime.context.user_id
    name = "John Smith" if user_id == "user_123" else "Unknown user"
    return Command(update={
        "user_name": name,
        # update the message history
        "messages": [
            ToolMessage(
                "Successfully looked up user information",
                tool_call_id=runtime.tool_call_id
            )
        ]
    })

@tool
def greet(
    runtime: ToolRuntime[CustomContext, CustomState]
) -> str | Command:
    """Use this to greet the user once you found their info."""
    user_name = runtime.state.get("user_name", None)
    if user_name is None:
       return Command(update={
            "messages": [
                ToolMessage(
                    "Please call the 'update_user_info' tool it will get and update the user's name.",
                    tool_call_id=runtime.tool_call_id
                )
            ]
        })
    return f"Hello {user_name}!"

model = ChatOpenAI(
            api_key=SecretStr(settings.dashscope_api_key),
            base_url=settings.base_url,
            timeout=settings.request_timeout_seconds,
            model=settings.model_name,
        )

agent = create_agent(
    model,
    tools=[get_user_info,update_user_info, greet],
    # checkpointer=InMemorySaver(),
    state_schema=CustomState,
    context_schema=CustomContext,
)

# class AgentState(TypedDict):
#     messages: list[AnyMessage | dict[str, Any]]

def main() -> None:

    # thread_config: RunnableConfig = {"configurable": {"thread_id": "1"}}

    # input_state: AgentState = {"messages": [HumanMessage(content="Hi! My name is Bob.")]}

    # response = agent.invoke(input_state, config=thread_config)["messages"][-1].content

    # print(response)

    # input_state = {"messages": [HumanMessage(content="What's my name?")]}
    # response = agent.invoke(input_state, config=thread_config)["messages"][-1].content

    # print(response)  # "You are Bob!"
    input_state = {"messages": [{"role": "user", "content": "greet the user"}]} 
    response = agent.invoke(cast(Any, input_state), context=CustomContext(user_id="user_123"))

    print(response)  # "You are Bob!"




if __name__ == "__main__":
    main()

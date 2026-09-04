import sys
from pathlib import Path

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import ToolRuntime
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from pydantic import SecretStr

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # examples/enterprise_alert_agent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import settings


# Access memory
@tool
def get_user_info(user_id: str, runtime: ToolRuntime) -> str:
    """Look up user info."""
    store: BaseStore | None = runtime.store
    # 判空，解决None无get属性报错
    if store is None:
        return "Unknown user"
    item = store.get(("users",), user_id)
    if item is None:
        return "Unknown user"
    return str(item.value)


@tool
def save_user_info(user_id: str, user_info: dict, runtime: ToolRuntime) -> str:
    """Save user info."""
    store: BaseStore | None = runtime.store
    if store is None:
        return "Save failed: store not initialized"
    store.put(("users",), user_id, user_info)
    return "Successfully saved user info."


model = ChatOpenAI(
    api_key=SecretStr(settings.dashscope_api_key),
    base_url=settings.dashscope_base_url,
    timeout=settings.request_timeout_seconds,
    model=settings.model_name,
)

store = InMemoryStore()
agent = create_agent(model, tools=[get_user_info, save_user_info], store=store)

# First session: save user info
# agent.invoke({
#     "messages": [{"role": "user", "content": "Save the following user: userid: abc123, name: Foo, age: 25, email: foo@langchain.dev"}]
# })

# # Second session: get user info
# agent.invoke({
#     "messages": [{"role": "user", "content": "Get user info for user with id 'abc123'"}]
# })
# Here is the user info for user with ID "abc123":
# - Name: Foo
# - Age: 25
# - Email: foo@langchain.dev


def main() -> None:
    # First session: save user info
    result1 = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "Save the following user: userid: abc123, name: Foo, age: 25, email: foo@langchain.dev",
                }
            ]
        }
    )
    print(result1)
    # Second session: get user info
    result2 = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "Get user info for user with id 'abc123'",
                }
            ]
        }
    )
    print(result2)


if __name__ == "__main__":
    main()

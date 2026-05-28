from langsmith import traceable
from openai import APIConnectionError, APIError, AuthenticationError, OpenAI

from app.config.settings import settings


class ModelClient:
    def __init__(self) -> None:
        self._client = OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.base_url,
            timeout=settings.request_timeout_seconds,
        )

    @traceable
    def chat(self, user_query: str, system_prompt: str) -> str:
        try:
            completion = self._client.chat.completions.create(
                model=settings.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_query},
                ],
                temperature=0.1,
            )
        except AuthenticationError as exc:
            raise ModelAuthError("Model authentication failed") from exc
        except (APIConnectionError, APIError) as exc:
            raise ModelRequestError("Model request failed") from exc

        content = completion.choices[0].message.content
        return content or ""

    def probe(self) -> None:
        """Run a lightweight provider check during service startup."""
        try:
            self._client.chat.completions.create(
                model=settings.model_name,
                messages=[
                    {"role": "system", "content": "health-check"},
                    {"role": "user", "content": "ping"},
                ],
                temperature=0,
                max_tokens=1,
            )
        except AuthenticationError as exc:
            raise ModelAuthError("Model authentication failed") from exc
        except (APIConnectionError, APIError) as exc:
            raise ModelRequestError("Model request failed") from exc


class ModelAuthError(Exception):
    """Raised when model provider authentication fails."""


class ModelRequestError(Exception):
    """Raised when model request fails for transient/provider reasons."""

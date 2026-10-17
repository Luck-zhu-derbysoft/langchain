"""Alert webhook retries must be bounded and safe to replay."""

import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import httpx

from app.observability.alert_manager import AlertManager
from app.observability.alert_types import Alert, AlertSeverity, AlertTypes


class TestAlertWebhookRetry(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.manager = object.__new__(AlertManager)
        self.alert = Alert(
            alert_id="alert-retry-1",
            alert_type=AlertTypes.FAULT_ALERT,
            severity=AlertSeverity.WARNING,
            title="Transient failure",
            message="Webhook delivery test",
            affected_resource="model-api",
        )

    @staticmethod
    def _response(status_code: int) -> httpx.Response:
        return httpx.Response(
            status_code,
            request=httpx.Request("POST", "https://alerts.example.test/hook"),
        )

    def _client_context(self, post: AsyncMock) -> Mock:
        client = Mock()
        client.post = post
        context = Mock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=None)
        return context

    async def test_retries_transient_status_and_reuses_idempotency_key(self) -> None:
        post = AsyncMock(side_effect=[self._response(503), self._response(200)])

        with (
            patch(
                "app.observability.alert_manager.settings.alert_webhook_url",
                "https://alerts.example.test/hook",
            ),
            patch(
                "app.observability.alert_manager.settings.alert_webhook_max_attempts",
                3,
                create=True,
            ),
            patch(
                "app.observability.alert_manager.settings.alert_webhook_retry_backoff_seconds",
                0.1,
                create=True,
            ),
            patch(
                "app.observability.alert_manager.settings.alert_webhook_retry_backoff_cap_seconds",
                1.0,
                create=True,
            ),
            patch(
                "app.observability.alert_manager.httpx.AsyncClient",
                return_value=self._client_context(post),
            ),
            patch("app.observability.alert_manager.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            await self.manager._send_internal_notification(self.alert)

        self.assertEqual(post.await_count, 2)
        sleep.assert_awaited_once()
        for call in post.await_args_list:
            self.assertEqual(call.kwargs["headers"]["Idempotency-Key"], self.alert.alert_id)

    async def test_does_not_retry_non_retryable_client_error(self) -> None:
        post = AsyncMock(return_value=self._response(400))

        with (
            patch(
                "app.observability.alert_manager.settings.alert_webhook_url",
                "https://alerts.example.test/hook",
            ),
            patch(
                "app.observability.alert_manager.settings.alert_webhook_max_attempts",
                3,
                create=True,
            ),
            patch(
                "app.observability.alert_manager.httpx.AsyncClient",
                return_value=self._client_context(post),
            ),
            patch("app.observability.alert_manager.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            await self.manager._send_internal_notification(self.alert)

        post.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_stops_after_timeout_attempts_are_exhausted(self) -> None:
        post = AsyncMock(side_effect=httpx.ReadTimeout("webhook timed out"))

        with (
            patch(
                "app.observability.alert_manager.settings.alert_webhook_url",
                "https://alerts.example.test/hook",
            ),
            patch(
                "app.observability.alert_manager.settings.alert_webhook_max_attempts",
                3,
                create=True,
            ),
            patch(
                "app.observability.alert_manager.settings.alert_webhook_retry_backoff_seconds",
                0.1,
                create=True,
            ),
            patch(
                "app.observability.alert_manager.settings.alert_webhook_retry_backoff_cap_seconds",
                1.0,
                create=True,
            ),
            patch(
                "app.observability.alert_manager.httpx.AsyncClient",
                return_value=self._client_context(post),
            ),
            patch("app.observability.alert_manager.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            await asyncio.wait_for(self.manager._send_internal_notification(self.alert), timeout=1)

        self.assertEqual(post.await_count, 3)
        self.assertEqual(sleep.await_count, 2)

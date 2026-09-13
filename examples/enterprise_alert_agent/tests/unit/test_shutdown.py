"""Exercise the shutdown callback without importing external application clients."""

import ast
import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch


class TestShutdown(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.events: list[str] = []
        self.checkpointer = object()
        self.memory = SimpleNamespace(aclose=AsyncMock())
        self.model = SimpleNamespace(aclose=AsyncMock())
        self.stack = SimpleNamespace(
            aclose=AsyncMock(side_effect=lambda: self.events.append("checkpoint"))
        )
        self.worker = SimpleNamespace(
            close=AsyncMock(side_effect=lambda: self.events.append("worker"))
        )
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                shared_dependencies={
                    "fault_checkpointer": self.checkpointer,
                    "memory": self.memory,
                    "model_client": self.model,
                },
                stream_worker=self.worker,
            )
        )
        self.drain = AsyncMock(return_value=True)
        self.dlq = SimpleNamespace(close=AsyncMock())
        self.close_mcp = AsyncMock()
        self.logger = Mock()
        modules = {
            "app.infrastructure.queue.dlq_handler": SimpleNamespace(dead_letter_queue=self.dlq),
            "app.infrastructure.mcp.mcp_client": SimpleNamespace(async_close_mcp=self.close_mcp),
        }
        module_patch = patch.dict("sys.modules", modules)
        module_patch.start()
        self.addCleanup(module_patch.stop)

        path = Path(__file__).resolve().parents[2] / "app" / "main.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        callback = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "shutdown_resources"
        )
        callback.decorator_list = []
        module = ast.fix_missing_locations(ast.Module(body=[callback], type_ignores=[]))
        namespace: dict[str, Any] = {
            "asyncio": asyncio,
            "logger": self.logger,
            "app": self.app,
            "checkpoint_stack": self.stack,
            "settings": SimpleNamespace(graceful_shutdown_timeout_seconds=1),
            "request_semaphore": object(),
            "_drain_inflight": self.drain,
            "audit_logger": Mock(),
        }
        exec(compile(module, str(path), "exec"), namespace)
        self.shutdown = cast(Callable[[], Awaitable[None]], namespace["shutdown_resources"])

    def assert_checkpoint_released(self) -> None:
        self.stack.aclose.assert_awaited_once()
        self.assertIsNone(self.app.state.shared_dependencies["fault_checkpointer"])

    async def test_normal_shutdown_closes_checkpoint_last(self) -> None:
        await self.shutdown()
        self.memory.aclose.assert_awaited_once()
        self.model.aclose.assert_awaited_once()
        self.dlq.close.assert_awaited_once()
        self.close_mcp.assert_awaited_once()
        self.assertEqual(self.events, ["worker", "checkpoint"])
        self.assert_checkpoint_released()

    async def test_drain_failure_still_releases_checkpoint(self) -> None:
        self.drain.side_effect = RuntimeError("drain failed")
        with self.assertRaisesRegex(RuntimeError, "drain failed"):
            await self.shutdown()
        self.assert_checkpoint_released()

    async def test_memory_failure_still_releases_checkpoint(self) -> None:
        self.memory.aclose.side_effect = RuntimeError("memory failed")
        with self.assertRaisesRegex(RuntimeError, "memory failed"):
            await self.shutdown()
        self.assert_checkpoint_released()

    async def test_cancellation_still_releases_checkpoint(self) -> None:
        self.drain.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.shutdown()
        self.assert_checkpoint_released()

    async def test_checkpoint_failure_still_clears_reference(self) -> None:
        self.stack.aclose.side_effect = RuntimeError("checkpoint failed")
        with self.assertRaisesRegex(RuntimeError, "checkpoint failed"):
            await self.shutdown()
        self.assert_checkpoint_released()

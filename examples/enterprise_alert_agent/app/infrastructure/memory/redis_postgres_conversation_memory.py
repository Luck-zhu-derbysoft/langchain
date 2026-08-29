import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import redis.asyncio as redis_asyncio
from psycopg import sql
from psycopg_pool import AsyncConnectionPool
from redis.exceptions import RedisError

from app.config.settings import settings
from app.infrastructure.fault.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryScope:
    tenant_id: str
    user_id: str
    thread_id: str


@dataclass
class MemoryContext:
    summary: str
    recent_turns: list[dict[str, str]]
    turn_count: int

    def as_prompt_text(self) -> str:
        parts: list[str] = []
        if self.summary.strip():
            parts.append(f"长期记忆摘要: {self.summary.strip()}")
        if self.recent_turns:
            recent_text = "\n".join(
                f"{turn['role'].capitalize()}: {turn['content'].strip()}"
                for turn in self.recent_turns
            )
            parts.append(f"近期对话:\n{recent_text}")
        return "\n\n".join(parts).strip()


class PersistentConversationMemoryStore:
    def load_context(self, scope: MemoryScope, *, max_turns: int) -> MemoryContext:
        # 从 Redis 和 PostgreSQL 加载上下文
        # 1. 从 Redis 获取近期对话
        # 2. 从 PostgreSQL 获取长期记忆摘要
        # 3. 组合成 MemoryContext 返回
        raise NotImplementedError

    def append_turn(
        self,
        scope: MemoryScope,
        *,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError

    def save_summary(self, scope: MemoryScope, summary: str) -> None:
        raise NotImplementedError

    def clear_memory(self, scope: MemoryScope) -> None:
        raise NotImplementedError


class RedisPostgresConversationMemoryStore(PersistentConversationMemoryStore):
    def __init__(self) -> None:
        # 初始化 Redis 和 PostgreSQL 连接
        self._redis_client = redis_asyncio.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        # PostgreSQL 连接池
        self._pg_pool = AsyncConnectionPool(
            conninfo=f"postgresql://{settings.pg_user}:{settings.pg_password}@{settings.pg_host}:{settings.pg_port}/{settings.pg_db}",
            min_size=2,
            max_size=10,
            timeout=5,  # `从连接池拿一个连接，拿不到会等待 timeout=5 秒超时抛异常
        )
        self._pg_circuit = CircuitBreaker(
            name="postgresql",
            failure_threshold=settings.circuit_breaker_failure_threshold,
            recovery_seconds=settings.circuit_breaker_recovery_seconds,
        )
        self._redis_circuit = CircuitBreaker(
            name="redis",
            failure_threshold=settings.circuit_breaker_failure_threshold,
            recovery_seconds=settings.circuit_breaker_recovery_seconds,
        )

        self._ttl_days = settings.memory_ttl_days
        self._cache_ttl = settings.redis_cache_ttl_seconds
        self._redact_pii = settings.memory_redact_pii

    def _scope_key(self, scope: MemoryScope) -> str:
        return f"memory:{scope.tenant_id}:{scope.user_id}:{scope.thread_id}"

    def _turns_key(self, scope: MemoryScope) -> str:
        return f"turns:{scope.tenant_id}:{scope.user_id}:{scope.thread_id}"

    async def awarmup(self) -> None:
        """启动时等待 PG 连接池填充到 min_size（psycopg_pool>=3.2 无 fill()，用 wait()）。"""
        try:
            await self._pg_pool.wait(timeout=5.0)
        except Exception:
            logger.warning(
                "PG connection pool warm-up timed out, connections created lazily",
                exc_info=True,
            )

    @asynccontextmanager
    async def _apg_conn(self) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        """异步上下文管理器，获取 PostgreSQL 连接并处理事务和电路断路器。"""
        self._pg_circuit.before_call()
        conn = None
        try:
            conn = await self._pg_pool.getconn()
            yield conn
            await conn.commit()
            self._pg_circuit.record_success()
        except Exception:
            if conn is not None:
                await conn.rollback()
            self._pg_circuit.record_failure()
            raise
        finally:
            if conn is not None:
                await self._pg_pool.putconn(conn)

    async def aclose(self) -> None:
        """Close Redis and PostgreSQL resources during application shutdown."""
        await self._redis_client.aclose()
        await self._pg_pool.close()

    async def aload_context(self, scope: MemoryScope, *, max_turns: int) -> MemoryContext:
        summary = ""
        recent_turns: list[dict[str, str]] = []
        scope_key = self._scope_key(scope)
        # 1) Redis 读（熔断保护）
        self._redis_circuit.before_call()
        try:
            cached = await self._redis_client.get(scope_key)
            self._redis_circuit.record_success()
            if cached:
                data = json.loads(cached)
                return MemoryContext(
                    summary=data.get("summary", ""),
                    recent_turns=data.get("recent_turns", []),
                    turn_count=data.get("turn_count", 0),
                )
        except CircuitOpenError:
            logger.warning("Redis circuit open, falling back to PG")
        except (RedisError, TimeoutError, ValueError):
            self._redis_circuit.record_failure()
            logger.warning("Redis cache read failed, falling back to PG: %s", exc_info=True)
        # 2) PG 读
        async with self._apg_conn() as conn, conn.cursor() as cur:
            await cur.execute(
                sql.SQL(
                    """
                    SELECT memory_summary, turn_count FROM conversation_memory_session
                    WHERE tenant_id = %s AND user_id = %s AND thread_id = %s
                    AND status = 'active' AND (expires_at IS NULL OR expires_at > NOW())
                    """
                ),
                (scope.tenant_id, scope.user_id, scope.thread_id),
            )
            rows = await cur.fetchone()
            summary = rows[0] if rows else ""
            turn_count = rows[1] if rows else 0
            await cur.execute(
                sql.SQL(
                    """
                    SELECT role, content FROM conversation_memory_turn
                    WHERE tenant_id = %s AND user_id = %s AND thread_id = %s
                    AND is_deleted = FALSE ORDER BY turn_index DESC LIMIT %s
                    """
                ),
                (scope.tenant_id, scope.user_id, scope.thread_id, max_turns * 2),
            )
            recent_rows = list(reversed((await cur.fetchall()) or []))
            recent_turns = [
                {"role": role, "content": self._sanitize(content)} for role, content in recent_rows
            ]

        # 3) 回写 Redis 缓存（熔断保护）
        self._redis_circuit.before_call()
        try:
            cache_data = {
                "summary": summary,
                "recent_turns": recent_turns,
                "turn_count": turn_count,
            }
            await self._redis_client.setex(scope_key, self._cache_ttl, json.dumps(cache_data))
            self._redis_circuit.record_success()
        except (RedisError, TimeoutError, ValueError):
            self._redis_circuit.record_failure()
            logger.warning("Redis cache write-back failed: %s", exc_info=True)

        return MemoryContext(summary=summary, recent_turns=recent_turns, turn_count=turn_count)

    async def aappend_turn(
        self,
        scope: MemoryScope,
        *,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        safe_content = self._sanitize(content) if self._redact_pii else content
        expires_at = datetime.now(UTC) + timedelta(days=self._ttl_days)
        scope_key = self._scope_key(scope)
        # PG 写
        async with self._apg_conn() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO conversation_memory_session
                (tenant_id, user_id, thread_id, memory_summary, turn_count, status, last_message_at, expires_at)
                VALUES (%s, %s, %s, '', 0, 'active', NOW(), %s)
                ON CONFLICT (tenant_id, user_id, thread_id)
                DO UPDATE SET last_message_at = NOW(), expires_at = EXCLUDED.expires_at
                """,
                (scope.tenant_id, scope.user_id, scope.thread_id, expires_at),
            )
            await cur.execute(
                """
                SELECT turn_count FROM conversation_memory_session
                WHERE tenant_id = %s AND user_id = %s AND thread_id = %s FOR UPDATE
                """,
                (scope.tenant_id, scope.user_id, scope.thread_id),
            )
            row = await cur.fetchone()
            next_turn = (row[0] if row else 0) + 1
            await cur.execute(
                """
                INSERT INTO conversation_memory_turn
                (tenant_id, user_id, thread_id, turn_index, role, content, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    scope.tenant_id,
                    scope.user_id,
                    scope.thread_id,
                    next_turn,
                    role,
                    safe_content,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            await cur.execute(
                """
                UPDATE conversation_memory_session
                SET turn_count = %s, last_message_at = NOW(), version = version + 1
                WHERE tenant_id = %s AND user_id = %s AND thread_id = %s
                """,
                (next_turn, scope.tenant_id, scope.user_id, scope.thread_id),
            )
        # Redis 写（如果存在）
        # Redis 缓存更新（熔断保护）
        self._redis_circuit.before_call()
        try:
            cached = await self._redis_client.get(scope_key)
            cache_data = json.loads(cached) if cached else {"summary": "", "recent_turns": []}
            turns = cache_data.get("recent_turns", [])
            turns.append({"role": role, "content": safe_content})
            if len(turns) > settings.cache_recent_turns_limit:
                turns = turns[-settings.cache_recent_turns_limit :]
            cache_data["recent_turns"] = turns
            cache_data["turn_count"] = next_turn
            await self._redis_client.setex(scope_key, self._cache_ttl, json.dumps(cache_data))
            self._redis_circuit.record_success()
        except (RedisError, TimeoutError, ValueError) as e:
            self._redis_circuit.record_failure()
            logger.warning("Redis cache update failed: %s", e)

    async def asave_summary(self, scope: MemoryScope, summary: str) -> None:
        safe_summary = self._sanitize(summary) if self._redact_pii else summary
        scope_key = self._scope_key(scope)

        async with self._apg_conn() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                    UPDATE conversation_memory_session
                    SET memory_summary = %s, last_message_at = NOW(), version = version + 1
                    WHERE tenant_id = %s AND user_id = %s AND thread_id = %s
                    RETURNING turn_count
                    """,
                (safe_summary, scope.tenant_id, scope.user_id, scope.thread_id),
            )
            row = await cur.fetchone()
            turn_count = row[0] if row else 0

        self._redis_circuit.before_call()
        try:
            cached = await self._redis_client.get(scope_key)
            cache_data = (
                json.loads(cached)
                if cached
                else {"summary": "", "recent_turns": [], "turn_count": 0}
            )
            cache_data["summary"] = safe_summary
            cache_data["turn_count"] = turn_count
            await self._redis_client.setex(scope_key, self._cache_ttl, json.dumps(cache_data))
            self._redis_circuit.record_success()
        except (RedisError, TimeoutError, ValueError) as e:
            self._redis_circuit.record_failure()
            logger.warning("Redis cache write failed: %s", e)

    async def aclear_memory(self, scope: MemoryScope) -> None:
        async with self._apg_conn() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                    UPDATE conversation_memory_session
                    SET status = 'deleted', last_message_at = NOW(), version = version + 1
                    WHERE tenant_id = %s AND user_id = %s AND thread_id = %s
                    """,
                (scope.tenant_id, scope.user_id, scope.thread_id),
            )
            await cur.execute(
                """
                    UPDATE conversation_memory_turn
                    SET is_deleted = TRUE
                    WHERE tenant_id = %s AND user_id = %s AND thread_id = %s
                    """,
                (scope.tenant_id, scope.user_id, scope.thread_id),
            )
        scope_key = self._scope_key(scope)
        self._redis_circuit.before_call()
        try:
            await self._redis_client.delete(scope_key)
            self._redis_circuit.record_success()
        except (RedisError, TimeoutError, ValueError) as e:
            self._redis_circuit.record_failure()
            logger.warning("Redis cache delete failed: %s", e)

    def _sanitize(self, text: str) -> str:
        value = text or ""
        value = re.sub(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
            "[REDACTED_EMAIL]",
            value,
        )
        value = re.sub(r"1[3-9]\d{9}", "[REDACTED_PHONE]", value)
        value = re.sub(r"\b\d{15,18}[0-9Xx]?\b", "[REDACTED_ID]", value)
        return value

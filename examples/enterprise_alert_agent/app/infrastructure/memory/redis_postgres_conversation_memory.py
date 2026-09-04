# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOperatorIssue=false
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import redis.asyncio as redis_asyncio
from redis.exceptions import RedisError
from sqlalchemy import Table, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from app.config.settings import settings
from app.infrastructure.fault.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.infrastructure.memory.models import ConversationMemorySession, ConversationMemoryTurn

logger = logging.getLogger(__name__)

SESSION_TABLE = cast(Table, vars(ConversationMemorySession)["__table__"])
TURN_TABLE = cast(Table, vars(ConversationMemoryTurn)["__table__"])


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
        self._pg_engine: AsyncEngine = create_async_engine(
            f"postgresql+asyncpg://{settings.pg_user}:{settings.pg_password}@{settings.pg_host}:{settings.pg_port}/{settings.pg_db}",
            pool_size=2,
            max_overflow=8,  # min_size=2, max_size=10 等价拆分
            pool_timeout=5,
            pool_pre_ping=True,
        )
        self._pg_session_factory = async_sessionmaker(self._pg_engine, expire_on_commit=False)

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
        """Initialize SQLModel tables and verify that the engine can connect."""
        try:
            async with self._pg_engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)
        except Exception:
            logger.warning(
                "PostgreSQL engine warm-up failed; connections will be created lazily",
                exc_info=True,
            )

    @asynccontextmanager
    async def _apg_session(self) -> AsyncIterator[AsyncSession]:
        """Provide a transactional SQLModel session protected by the circuit breaker."""
        self._pg_circuit.before_call()
        session = self._pg_session_factory()
        try:
            yield session
            await session.commit()
            self._pg_circuit.record_success()
        except Exception:
            if session is not None:
                await session.rollback()
            self._pg_circuit.record_failure()
            raise
        finally:
            if session is not None:
                await session.close()

    async def aclose(self) -> None:
        """Close Redis and PostgreSQL resources during application shutdown."""
        await self._redis_client.aclose()
        await self._pg_engine.dispose()

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
        async with self._apg_session() as session:
            session_row = (
                await session.execute(
                    select(ConversationMemorySession).where(
                        SESSION_TABLE.c.tenant_id == scope.tenant_id,
                        SESSION_TABLE.c.user_id == scope.user_id,
                        SESSION_TABLE.c.thread_id == scope.thread_id,
                        SESSION_TABLE.c.status == "active",
                        or_(
                            SESSION_TABLE.c.expires_at.is_(None),
                            SESSION_TABLE.c.expires_at > datetime.now(UTC),
                        ),
                    )
                )
            ).scalar_one_or_none()
            summary = session_row.memory_summary if session_row else ""
            turn_count = session_row.turn_count if session_row else 0
            recent_rows = list(
                reversed(
                    (
                        await session.execute(
                            select(ConversationMemoryTurn)
                            .where(
                                TURN_TABLE.c.tenant_id == scope.tenant_id,
                                TURN_TABLE.c.user_id == scope.user_id,
                                TURN_TABLE.c.thread_id == scope.thread_id,
                                TURN_TABLE.c.is_deleted.is_(False),
                            )
                            .order_by(TURN_TABLE.c.turn_index.desc())
                            .limit(max_turns * 2)
                        )
                    )
                    .scalars()
                    .all()
                )
            )
            recent_turns = [
                {"role": row.role, "content": self._sanitize(row.content)} for row in recent_rows
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
        async with self._apg_session() as session:
            now = datetime.now(UTC)
            await session.execute(
                pg_insert(ConversationMemorySession)
                .values(
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    thread_id=scope.thread_id,
                    memory_summary="",
                    turn_count=0,
                    status="active",
                    last_message_at=now,
                    expires_at=expires_at,
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "user_id", "thread_id"],
                    set_={"last_message_at": now, "expires_at": expires_at},
                )
            )
            session_row = (
                await session.execute(
                    select(ConversationMemorySession)
                    .where(
                        SESSION_TABLE.c.tenant_id == scope.tenant_id,
                        SESSION_TABLE.c.user_id == scope.user_id,
                        SESSION_TABLE.c.thread_id == scope.thread_id,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            next_turn = session_row.turn_count + 1
            session.add(
                ConversationMemoryTurn(
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    thread_id=scope.thread_id,
                    turn_index=next_turn,
                    role=role,
                    content=safe_content,
                    metadata_=metadata or {},
                )
            )
            session_row.turn_count = next_turn
            session_row.last_message_at = now
            session_row.version += 1
        # Redis 写（如果存在）
        # Redis 缓存更新（熔断保护）
        self._redis_circuit.before_call()
        try:
            cached = await self._redis_client.get(scope_key)
            cache_data = json.loads(cached) if cached else {"summary": "", "recent_turns": []}
            turns = cast(list[dict[str, str]], cache_data.get("recent_turns", []))
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

        async with self._apg_session() as session:
            session_row = (
                await session.execute(
                    select(ConversationMemorySession).where(
                        SESSION_TABLE.c.tenant_id == scope.tenant_id,
                        SESSION_TABLE.c.user_id == scope.user_id,
                        SESSION_TABLE.c.thread_id == scope.thread_id,
                    )
                )
            ).scalar_one_or_none()
            turn_count = session_row.turn_count if session_row else 0
            if session_row is not None:
                session_row.memory_summary = safe_summary
                session_row.last_message_at = datetime.now(UTC)
                session_row.version += 1

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
        async with self._apg_session() as session:
            await session.execute(
                update(ConversationMemorySession)
                .where(
                    SESSION_TABLE.c.tenant_id == scope.tenant_id,
                    SESSION_TABLE.c.user_id == scope.user_id,
                    SESSION_TABLE.c.thread_id == scope.thread_id,
                )
                .values(
                    status="deleted",
                    last_message_at=datetime.now(UTC),
                    version=SESSION_TABLE.c.version + 1,
                )
            )
            await session.execute(
                update(ConversationMemoryTurn)
                .where(
                    TURN_TABLE.c.tenant_id == scope.tenant_id,
                    TURN_TABLE.c.user_id == scope.user_id,
                    TURN_TABLE.c.thread_id == scope.thread_id,
                )
                .values(is_deleted=True)
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

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg
import redis
from psycopg import sql
from psycopg_pool import ConnectionPool

from app.config.settings import settings

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
        self._redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        # PostgreSQL 连接池
        self._pg_pool = ConnectionPool(
            conninfo=f"postgresql://{settings.pg_user}:{settings.pg_password}@{settings.pg_host}:{settings.pg_port}/{settings.pg_db}",
            min_size=2,
            max_size=10,
            timeout=5,  # `从连接池拿一个连接，拿不到会等待 timeout=5 秒超时抛异常
        )

        self._ttl_days = settings.memory_ttl_days
        self._cache_ttl = settings.redis_cache_ttl_seconds
        self._redact_pii = settings.memory_redact_pii

    def _scope_key(self, scope: MemoryScope) -> str:
        return f"memory:{scope.tenant_id}:{scope.user_id}:{scope.thread_id}"

    def _turns_key(self, scope: MemoryScope) -> str:
        return f"turns:{scope.tenant_id}:{scope.user_id}:{scope.thread_id}"

    @contextmanager
    def _pg_conn(self) -> Iterator[psycopg.Connection]:
        # ConnectionPool 只是创建对象，不会预创建连接，第一次getconn才真正建立 pg 连接；
        # 如果是长驻留服务，建议启动时做一次预热
        conn = None
        try:
            conn = self._pg_pool.getconn()
            yield conn
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise

        finally:
            if conn is not None:
                self._pg_pool.putconn(conn)

    def __del__(self) -> None:
        if hasattr(self, "_pg_pool"):
            self._pg_pool.close()

    def load_context(self, scope: MemoryScope, *, max_turns: int) -> MemoryContext:
        summary = ""
        recent_turns: list[dict[str, str]] = []

        # 从 Redis 加载近期对话
        scope_key = self._scope_key(scope)
        try:
            cached = self._redis_client.get(scope_key)  # 触发连接检查
            if cached:
                data = json.loads(cached)
                return MemoryContext(
                    summary=data.get("summary", ""),
                    recent_turns=data.get("recent_turns", []),
                    turn_count=data.get("turn_count", 0),
                )
        except Exception:
            logger.warning("Redis cache read failed, falling back to PG: %s", exc_info=True)
        # 如果 Redis 中没有有效缓存，则从 PostgreSQL 加载近期对话
        with self._pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT memory_summary, turn_count FROM conversation_memory_session
                        WHERE tenant_id = %s AND user_id = %s AND thread_id = %s
                        and status = 'active' and (expires_at IS NULL OR expires_at > NOW())
                        """
                    ),
                    (scope.tenant_id, scope.user_id, scope.thread_id),
                )
                rows = cur.fetchone()
                summary = rows[0] if rows else ""
                turn_count = rows[1] if rows else 0
                # 加载近期对话
                cur.execute(
                    sql.SQL(
                        """
                        SELECT role, content FROM conversation_memory_turn
                        WHERE tenant_id = %s AND user_id = %s AND thread_id = %s
                        AND is_deleted = FALSE ORDER BY turn_index DESC
                        LIMIT %s
                        """
                    ),
                    (scope.tenant_id, scope.user_id, scope.thread_id, max_turns * 2),
                )
                recent_rows = list(reversed(cur.fetchall() or []))
            recent_turns = [
                {"role": role, "content": self._sanitize(content)} for role, content in recent_rows
            ]

            try:
                # 将加载的上下文缓存到 Redis，设置 TTL
                cache_data = {
                    "summary": summary,
                    "recent_turns": recent_turns,
                    "turn_count": turn_count,
                }
                self._redis_client.setex(scope_key, self._cache_ttl, json.dumps(cache_data))
            except Exception:
                pass
        return MemoryContext(summary=summary, recent_turns=recent_turns, turn_count=turn_count)

    def append_turn(
        self, scope: MemoryScope, *, role: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        safe_content = self._sanitize(content) if self._redact_pii else content
        expires_at = datetime.utcnow() + timedelta(days=self._ttl_days)
        scope_key = self._scope_key(scope)

        # 将新对话追加到 PostgreSQL 中，并更新 Redis 缓存
        with self._pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversation_memory_session
                    (tenant_id, user_id, thread_id, memory_summary, turn_count, status, last_message_at, expires_at)
                    VALUES (%s, %s, %s, '', 0, 'active', NOW(), %s)
                    ON CONFLICT (tenant_id, user_id, thread_id)
                    DO UPDATE SET
                        last_message_at = NOW(),
                        expires_at = EXCLUDED.expires_at
                    """,
                    (scope.tenant_id, scope.user_id, scope.thread_id, expires_at),
                )

                cur.execute(
                    """
                    SELECT turn_count FROM conversation_memory_session
                    WHERE tenant_id = %s AND user_id = %s AND thread_id = %s
                    FOR UPDATE
                    """,
                    (scope.tenant_id, scope.user_id, scope.thread_id),
                )
                row = cur.fetchone()
                next_turn = (row[0] if row else 0) + 1

                cur.execute(
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

                cur.execute(
                    """
                    UPDATE conversation_memory_session
                    SET turn_count = %s, last_message_at = NOW(), version = version + 1
                    WHERE tenant_id = %s AND user_id = %s AND thread_id = %s
                    """,
                    (next_turn, scope.tenant_id, scope.user_id, scope.thread_id),
                )
        # 更新 Redis 缓存（如果存在）
        scope_key = self._scope_key(scope)
        try:
            cached = self._redis_client.get(scope_key)  # 触发连接检查
            if cached:
                cache_data = json.loads(cached)
            else:
                cache_data = {"summary": "", "recent_turns": []}
            turns = cache_data.get("recent_turns", [])
            turns.append({"role": role, "content": safe_content})
            if len(turns) > settings.cache_recent_turns_limit:  # 假设我们只缓存最近N轮对话
                turns = turns[-settings.cache_recent_turns_limit :]
            cache_data["recent_turns"] = turns
            cache_data["turn_count"] = next_turn
            self._redis_client.setex(scope_key, self._cache_ttl, json.dumps(cache_data))

        except Exception as e:
            logger.warning("Redis cache update failed: %s", e)

    def save_summary(self, scope: MemoryScope, summary: str) -> None:
        safe_summary = self._sanitize(summary) if self._redact_pii else summary
        with self._pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    UPDATE conversation_memory_session
                    SET memory_summary = %s, last_message_at = NOW(), version = version + 1
                    WHERE tenant_id = %s AND user_id = %s AND thread_id = %s
                    RETURNING turn_count
                    """,
                (safe_summary, scope.tenant_id, scope.user_id, scope.thread_id),
            )
            row = cur.fetchone()
            turn_count = row[0] if row else 0
        # 更新 Redis 缓存（如果存在）
        scope_key = self._scope_key(scope)
        try:
            cached = self._redis_client.get(scope_key)  # 触发连接检查
            if cached:
                cache_data = json.loads(cached)
            else:
                cache_data = {"summary": "", "recent_turns": [], "turn_count": 0}
            cache_data["summary"] = safe_summary
            cache_data["turn_count"] = turn_count
            self._redis_client.setex(scope_key, self._cache_ttl, json.dumps(cache_data))
        except Exception as e:
            logger.warning("Redis cache write failed: %s", e)

    def clear_memory(self, scope: MemoryScope) -> None:
        with self._pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    UPDATE conversation_memory_session
                    SET status = 'deleted', last_message_at = NOW(), version = version + 1
                    WHERE tenant_id = %s AND user_id = %s AND thread_id = %s
                    """,
                (scope.tenant_id, scope.user_id, scope.thread_id),
            )
            cur.execute(
                """
                    UPDATE conversation_memory_turn
                    SET is_deleted = TRUE
                    WHERE tenant_id = %s AND user_id = %s AND thread_id = %s
                    """,
                (scope.tenant_id, scope.user_id, scope.thread_id),
            )
        # 删除 Redis 缓存
        scope_key = self._scope_key(scope)
        try:
            self._redis_client.delete(scope_key)
        except Exception as e:
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

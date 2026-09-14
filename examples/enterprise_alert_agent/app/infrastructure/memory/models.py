from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Column, Index
from sqlmodel import Field, SQLModel


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ConversationMemorySession(SQLModel, table=True):
    __tablename__ = "conversation_memory_session"  # type: ignore

    tenant_id: str = Field(primary_key=True, max_length=128)
    user_id: str = Field(primary_key=True, max_length=128)
    thread_id: str = Field(primary_key=True, max_length=128)
    memory_summary: str = Field(default="")
    turn_count: int = Field(default=0)
    status: str = Field(default="active", max_length=16)
    version: int = Field(default=0)
    last_message_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationMemoryTurn(SQLModel, table=True):
    __tablename__ = "conversation_memory_turn"  # type: ignore
    __table_args__ = (
        Index(
            "uq_turn_scope_index",
            "tenant_id",
            "user_id",
            "thread_id",
            "turn_index",
            unique=True,
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(max_length=128)
    user_id: str = Field(max_length=128)
    thread_id: str = Field(max_length=128)
    turn_index: int
    role: str = Field(max_length=32)
    content: str
    metadata_: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    is_deleted: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentTaskState(SQLModel, table=True):
    __tablename__ = "agent_task_state"  # type: ignore
    __table_args__ = (
        Index("idx_task_state_request", "request_id", "created_at"),
        Index("idx_task_state_recovery", "status", "updated_at"),
    )

    request_id: str = Field(primary_key=True, max_length=128)
    task_id: str = Field(primary_key=True, max_length=128)
    tenant_id: str = Field(max_length=128)
    user_id: str = Field(max_length=128)
    thread_id: str = Field(max_length=128)
    status: str = Field(default=TaskStatus.QUEUED.value, max_length=32)
    description: str
    assigned_agent_id: str = Field(default="", max_length=128)
    depends_on: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    result: str = Field(default="")
    error_message: str = Field(default="")
    retry_count: int = Field(default=0)
    version: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

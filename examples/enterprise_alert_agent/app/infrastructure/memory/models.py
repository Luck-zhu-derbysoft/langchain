from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Index
from sqlmodel import Field, SQLModel


class ConversationMemorySession(SQLModel, table=True):
    __tablename__ = "conversation_memory_session"

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
    __tablename__ = "conversation_memory_turn"
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

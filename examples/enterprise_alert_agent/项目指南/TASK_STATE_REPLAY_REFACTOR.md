# 任务状态管理 / 状态机：执行快照与幂等重放改造参考

> 目标：把当前"崩溃后发现 queued/running 并转人工处理"的能力，升级为"崩溃后发现可重放任务，基于执行快照自动重放；无快照或重放失败再转人工处理"。

---

## 1. 当前边界

当前版本已经具备：

- `agent_task_state` 状态表
- `QUEUED / RUNNING / WAITING_HUMAN / SUCCEEDED / FAILED / SKIPPED` 状态枚举
- 多任务执行中的状态落库
- HITL 人工干预恢复
- 启动时扫描未完成任务并转人工处理

但还没有真正做到：

- 保存原始执行上下文
- 保存幂等键
- 启动后自动认领可重放任务
- 自动重建 `SubTask` 并执行
- 重放失败后兜底转 `WAITING_HUMAN`

所以当前能力更准确地说是：**崩溃后可发现并转人工处理**，不是完整的自动重放。

---

## 2. 改造目标

改造后流程：

```text
任务分解
  -> 写入 QUEUED + execution_snapshot + idempotency_key + replayable=True
  -> 执行前更新 RUNNING
  -> 成功/失败/跳过后 replayable=False

服务重启
  -> 扫描 replayable=True 且 QUEUED/RUNNING 的任务
  -> 认领 lease，避免多实例重复重放
  -> 从 execution_snapshot 重建子任务
  -> 自动重放
  -> 成功则落 SUCCEEDED
  -> 失败则转 WAITING_HUMAN 并告警
```

设计原则：

- 只自动重放有快照的任务。
- 只重放 `replayable=True` 的任务。
- 每个任务默认最多重放 1 次。
- 无快照、超过重放次数、重放失败，都转人工处理。
- 外部副作用必须依赖业务幂等键兜底，不能只靠状态表。

---

## 3. 修改 `models.py`

文件：`app/infrastructure/memory/models.py`

### 3.1 扩展索引

将 `AgentTaskState.__table_args__` 调整为：

```python
    __table_args__ = (
        Index("idx_task_state_request", "request_id", "created_at"),
        Index("idx_task_state_recovery", "status", "updated_at"),
        Index("idx_task_state_replay", "replayable", "status", "lease_expires_at"),
        Index("idx_task_state_idempotency", "idempotency_key"),
    )
```

### 3.2 增加执行快照字段

在 `retry_count` 后增加字段：

```python
    retry_count: int = Field(default=0)
    replay_count: int = Field(default=0)
    max_replay_count: int = Field(default=1)
    replayable: bool = Field(default=False)
    idempotency_key: str = Field(default="", max_length=256)
    execution_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    lease_owner: str = Field(default="", max_length=128)
    lease_expires_at: datetime | None = Field(default=None)
    version: int = Field(default=0)
```

完整字段片段参考：

```python
class AgentTaskState(SQLModel, table=True):
    __tablename__ = "agent_task_state"  # type: ignore
    __table_args__ = (
        Index("idx_task_state_request", "request_id", "created_at"),
        Index("idx_task_state_recovery", "status", "updated_at"),
        Index("idx_task_state_replay", "replayable", "status", "lease_expires_at"),
        Index("idx_task_state_idempotency", "idempotency_key"),
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
    replay_count: int = Field(default=0)
    max_replay_count: int = Field(default=1)
    replayable: bool = Field(default=False)
    idempotency_key: str = Field(default="", max_length=256)
    execution_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    lease_owner: str = Field(default="", max_length=128)
    lease_expires_at: datetime | None = Field(default=None)
    version: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

---

## 4. 修改建表 SQL

文件：`scripts/init_pg_memory_schema.sql`

### 4.1 更新建表字段

在 `agent_task_state` 表中，把 `retry_count` 附近字段调整为：

```sql
    result             TEXT NOT NULL DEFAULT '',
    error_message      TEXT NOT NULL DEFAULT '',
    retry_count        INTEGER NOT NULL DEFAULT 0,
    replay_count       INTEGER NOT NULL DEFAULT 0,
    max_replay_count   INTEGER NOT NULL DEFAULT 1,
    replayable         BOOLEAN NOT NULL DEFAULT FALSE,
    idempotency_key    VARCHAR(256) NOT NULL DEFAULT '',
    execution_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    lease_owner        VARCHAR(128) NOT NULL DEFAULT '',
    lease_expires_at   TIMESTAMPTZ,
    version            INTEGER NOT NULL DEFAULT 0,
```

### 4.2 增加索引

```sql
CREATE INDEX IF NOT EXISTS idx_task_state_replay
    ON agent_task_state (replayable, status, lease_expires_at);

CREATE INDEX IF NOT EXISTS idx_task_state_idempotency
    ON agent_task_state (idempotency_key);
```

### 4.3 已有数据库迁移 SQL

如果表已经在 PostgreSQL 中创建过，执行：

```sql
ALTER TABLE agent_task_state
    ADD COLUMN IF NOT EXISTS replay_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS max_replay_count INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS replayable BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(256) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS execution_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS lease_owner VARCHAR(128) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_task_state_replay
    ON agent_task_state (replayable, status, lease_expires_at);

CREATE INDEX IF NOT EXISTS idx_task_state_idempotency
    ON agent_task_state (idempotency_key);
```

---

## 5. 修改 `redis_postgres_conversation_memory.py`

文件：`app/infrastructure/memory/redis_postgres_conversation_memory.py`

### 5.1 扩展 `aupsert_task_state()` 参数

签名调整为：

```python
    async def aupsert_task_state(
        self,
        *,
        request_id: str,
        task_id: str,
        scope: MemoryScope,
        description: str,
        status: TaskStatus,
        assigned_agent_id: str = "",
        depends_on: list[str] | None = None,
        result: str = "",
        error_message: str = "",
        retry_count: int = 0,
        replayable: bool = False,
        idempotency_key: str = "",
        execution_snapshot: dict[str, Any] | None = None,
        max_replay_count: int = 1,
    ) -> None:
```

### 5.2 创建新任务时保存快照字段

在 `AgentTaskState(...)` 中补充：

```python
                    replayable=replayable,
                    idempotency_key=idempotency_key,
                    execution_snapshot=execution_snapshot or {},
                    max_replay_count=max_replay_count,
```

完整创建片段参考：

```python
                task_state = AgentTaskState(
                    request_id=request_id,
                    task_id=task_id,
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    thread_id=scope.thread_id,
                    description=description,
                    status=status.value,
                    assigned_agent_id=assigned_agent_id,
                    depends_on=depends_on or [],
                    result=result,
                    error_message=error_message,
                    retry_count=retry_count,
                    replayable=replayable,
                    idempotency_key=idempotency_key,
                    execution_snapshot=execution_snapshot or {},
                    max_replay_count=max_replay_count,
                    created_at=now,
                    updated_at=now,
                )
```

### 5.3 更新已有任务时维护快照字段

将更新分支调整为：

```python
                existing.description = description
                existing.status = status.value
                existing.assigned_agent_id = assigned_agent_id
                existing.depends_on = depends_on or existing.depends_on
                existing.result = result
                existing.error_message = error_message
                existing.retry_count = retry_count
                existing.replayable = replayable
                existing.idempotency_key = idempotency_key or existing.idempotency_key
                existing.execution_snapshot = execution_snapshot or existing.execution_snapshot
                existing.max_replay_count = max_replay_count
                existing.updated_at = now
                existing.version += 1
                return
```

注意：这里建议使用 `existing.replayable = replayable`，不要用 `replayable or existing.replayable`。否则任务结束时传入 `replayable=False` 会关不掉重放开关。

### 5.4 增加可重放任务认领方法

放在 `alist_incomplete_task_states()` 后面：

```python
    async def aclaim_replayable_task_states(
        self,
        *,
        owner: str,
        limit: int = 20,
        lease_seconds: int = 300,
    ) -> list[AgentTaskState]:
        now = datetime.now(UTC)
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        async with self._apg_session() as session:
            result = await session.execute(
                select(AgentTaskState)
                .where(
                    AgentTaskState.replayable.is_(True),
                    AgentTaskState.status.in_(
                        [
                            TaskStatus.QUEUED.value,
                            TaskStatus.RUNNING.value,
                        ]
                    ),
                    AgentTaskState.replay_count < AgentTaskState.max_replay_count,
                    or_(
                        AgentTaskState.lease_expires_at.is_(None),
                        AgentTaskState.lease_expires_at < now,
                    ),
                )
                .order_by(AgentTaskState.updated_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            claimed: list[AgentTaskState] = []
            for task in result.scalars().all():
                if not task.execution_snapshot:
                    continue
                task.lease_owner = owner
                task.lease_expires_at = lease_expires_at
                task.replay_count += 1
                task.status = TaskStatus.QUEUED.value
                task.updated_at = now
                task.version += 1
                claimed.append(task)
            return claimed
```

---

## 6. 修改 `chat_service.py`

文件：`app/application/services/chat_service.py`

### 6.1 增加导入

把 dataclass 导入改成：

```python
from dataclasses import asdict, dataclass, field
```

增加模型导入：

```python
from app.infrastructure.memory.models import AgentTaskState
```

### 6.2 增加快照 helper

建议放在 `_aexecute_decomposed_tasks()` 前：

```python
    @staticmethod
    def _task_idempotency_key(request_id: str, task_id: str) -> str:
        return f"{request_id}:{task_id}"

    @staticmethod
    def _build_task_execution_snapshot(
        *,
        req_query: str,
        subtask: SubTask,
        system_prompt: str,
        available_tools: list[dict[str, Any]],
        tool_selection: ToolSelection | None,
    ) -> dict[str, Any]:
        return {
            "request_query": req_query,
            "subtask": asdict(subtask),
            "system_prompt": system_prompt,
            "available_tools": available_tools,
            "tool_selection": asdict(tool_selection) if tool_selection else None,
        }
```

### 6.3 写入 `QUEUED` 时保存快照

找到：

```python
        for subtask in decomposition.subtasks:
            await self.memory.aupsert_task_state(
                request_id=request_id,
                task_id=subtask.task_id,
                scope=history_scope,
                description=subtask.description,
                status=TaskStatus.QUEUED,
                depends_on=subtask.depends_on,
            )
```

替换为：

```python
        for subtask in decomposition.subtasks:
            execution_snapshot = self._build_task_execution_snapshot(
                req_query=req_query,
                subtask=subtask,
                system_prompt=system_prompt,
                available_tools=available_tools,
                tool_selection=tool_selection,
            )
            await self.memory.aupsert_task_state(
                request_id=request_id,
                task_id=subtask.task_id,
                scope=history_scope,
                description=subtask.description,
                status=TaskStatus.QUEUED,
                depends_on=subtask.depends_on,
                replayable=True,
                idempotency_key=self._task_idempotency_key(request_id, subtask.task_id),
                execution_snapshot=execution_snapshot,
                max_replay_count=1,
            )
```

### 6.4 写入 `RUNNING` 时刷新快照

找到 `arun_one_task()` 里的第一次 `aupsert_task_state()`，替换为：

```python
            execution_snapshot = self._build_task_execution_snapshot(
                req_query=req_query,
                subtask=subtask,
                system_prompt=system_prompt,
                available_tools=available_tools,
                tool_selection=tool_selection,
            )
            await self.memory.aupsert_task_state(
                request_id=request_id,
                task_id=subtask.task_id,
                scope=history_scope,
                description=subtask.description,
                status=TaskStatus.RUNNING,
                depends_on=subtask.depends_on,
                assigned_agent_id=subtask.assigned_agent_id,
                replayable=True,
                idempotency_key=self._task_idempotency_key(request_id, subtask.task_id),
                execution_snapshot=execution_snapshot,
                max_replay_count=1,
            )
```

### 6.5 结束状态关闭重放

成功/失败落库时补 `replayable=False`：

```python
                await self.memory.aupsert_task_state(
                    request_id=request_id,
                    task_id=tid,
                    scope=history_scope,
                    description=next(
                        item.description for item in decomposition.subtasks if item.task_id == tid
                    ),
                    status=status,
                    assigned_agent_id=agent_id,
                    result=out if success else "",
                    error_message="" if success else out,
                    retry_count=retry_times,
                    replayable=False,
                )
```

依赖失败跳过时也建议补：

```python
                    await self.memory.aupsert_task_state(
                        request_id=request_id,
                        task_id=subtask.task_id,
                        scope=history_scope,
                        description=subtask.description,
                        status=TaskStatus.SKIPPED,
                        assigned_agent_id=subtask.assigned_agent_id,
                        result="",
                        error_message="Skipped due to failed dependencies",
                        retry_count=0,
                        depends_on=subtask.depends_on,
                        replayable=False,
                    )
```

HITL 挂起时建议保留快照，但不要自动重放：

```python
                await self.memory.aupsert_task_state(
                    request_id=request_id,
                    task_id=subtask.task_id,
                    scope=history_scope,
                    description=subtask.description,
                    status=TaskStatus.WAITING_HUMAN,
                    assigned_agent_id=subtask.assigned_agent_id,
                    depends_on=subtask.depends_on,
                    error_message=str(root_cause),
                    retry_count=outcome.attempts,
                    replayable=False,
                )
```

### 6.6 增加单任务快照重放方法

建议放在 `aresume_task()` 前：

```python
    async def areplay_task_from_snapshot(
        self,
        task: AgentTaskState,
    ) -> dict[str, Any]:
        snapshot = task.execution_snapshot or {}
        subtask_data = snapshot.get("subtask") or {}
        if not subtask_data:
            raise ValueError(f"Task snapshot is empty: {task.request_id}/{task.task_id}")

        subtask = SubTask(
            task_id=str(subtask_data["task_id"]),
            description=str(subtask_data["description"]),
            preferred_tool=str(subtask_data.get("preferred_tool", "")),
            depends_on=list(subtask_data.get("depends_on", [])),
            priority=int(subtask_data.get("priority", 0)),
            assigned_agent_id=str(subtask_data.get("assigned_agent_id", "")),
        )
        scope = MemoryScope(
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            thread_id=task.thread_id,
        )
        state = AgentState()
        tools_resolution = await self._aresolve_tools({"rag": True, "sql": True, "time": True})
        available_tools = list(snapshot.get("available_tools") or tools_resolution["available"])
        skill_map = tools_resolution["skill_map"]
        mcp_tool_map = tools_resolution["mcp_map"]

        tool_selection_data = snapshot.get("tool_selection")
        tool_selection = None
        if tool_selection_data:
            tool_selection = ToolSelection(
                tool_name=str(tool_selection_data.get("tool_name", "")),
                confidence=float(tool_selection_data.get("confidence", 0.0)),
                fallback_tools=list(tool_selection_data.get("fallback_tools", [])),
                reasoning=str(tool_selection_data.get("reasoning", "")),
                agent_id=str(tool_selection_data.get("agent_id", "")),
            )

        result = await self._aexecute_decomposed_tasks(
            req_query=str(snapshot.get("request_query") or task.description),
            request_id=task.request_id,
            decomposition=TaskDecomposition(
                subtasks=[subtask],
                parallel_groups=[[subtask.task_id]],
                dependencies={subtask.task_id: subtask.depends_on},
                strategy="replay_single",
            ),
            system_prompt=str(snapshot.get("system_prompt") or ""),
            available_tools=available_tools,
            skill_map=skill_map,
            mcp_tool_map=mcp_tool_map,
            history_scope=scope,
            tool_selection=tool_selection,
            state=state,
        )
        return {
            "request_id": task.request_id,
            "task_id": task.task_id,
            "status": result.task_status_mapping.get(task.task_id, ""),
            "success": task.task_id in result.task_outputs,
            "output": result.task_outputs.get(task.task_id, ""),
            "failed_task_ids": result.failed_task_ids,
        }
```

---

## 7. 修改 `main.py`

文件：`app/main.py`

### 7.1 增加导入

```python
from app.application.services.chat_service import ChatService
```

### 7.2 启动时优先自动重放

将当前启动恢复逻辑中"直接把 queued/running 转 `WAITING_HUMAN`"的部分，替换为：

```python
        await memory.awarmup()
        try:
            replay_owner = f"startup:{uuid.uuid4()}"
            replayable_tasks = await memory.aclaim_replayable_task_states(
                owner=replay_owner,
                limit=20,
                lease_seconds=300,
            )
            if replayable_tasks:
                replay_service = ChatService(
                    model_client=model_client,
                    retriever=retriever,
                    trace=trace,
                    memory=memory,
                    _intervention_handler=intervention_handler,
                    _alert_manager=alert_manager,
                    _metrics_collector=metrics_collector,
                    agent_registry=agent_registry,
                    orchestrator=orchestrator,
                    fault_checkpointer=fault_checkpoint,
                )
                for task in replayable_tasks:
                    try:
                        logger.info(
                            "Replaying task from snapshot: request_id=%s task_id=%s",
                            task.request_id,
                            task.task_id,
                        )
                        await replay_service.areplay_task_from_snapshot(task)
                    except Exception as replay_error:
                        logger.exception(
                            "Task replay failed: request_id=%s task_id=%s",
                            task.request_id,
                            task.task_id,
                        )
                        scope = MemoryScope(
                            tenant_id=task.tenant_id,
                            user_id=task.user_id,
                            thread_id=task.thread_id,
                        )
                        await memory.aupsert_task_state(
                            request_id=task.request_id,
                            task_id=task.task_id,
                            scope=scope,
                            description=task.description,
                            status=TaskStatus.WAITING_HUMAN,
                            assigned_agent_id=task.assigned_agent_id,
                            depends_on=task.depends_on,
                            result=task.result,
                            error_message=f"Replay failed after restart: {replay_error}",
                            retry_count=task.retry_count,
                            replayable=False,
                        )
                        alert_manager.create_alert(
                            alert_type=AlertTypes.FAULT_ALERT,
                            severity=AlertSeverity.CRITICAL,
                            title=f"任务自动重放失败 ({task.task_id})",
                            message=f"请求 {task.request_id} 的任务自动重放失败，已转人工处理",
                            affected_resource=task.task_id,
                            context={
                                "tenant_id": task.tenant_id,
                                "user_id": task.user_id,
                                "thread_id": task.thread_id,
                                "previous_status": task.status,
                            },
                        )

            incomplete_tasks = await memory.alist_incomplete_task_states()
            for task in incomplete_tasks:
                if task.status in (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value):
                    scope = MemoryScope(
                        tenant_id=task.tenant_id,
                        user_id=task.user_id,
                        thread_id=task.thread_id,
                    )
                    await memory.aupsert_task_state(
                        request_id=task.request_id,
                        task_id=task.task_id,
                        scope=scope,
                        description=task.description,
                        status=TaskStatus.WAITING_HUMAN,
                        assigned_agent_id=task.assigned_agent_id,
                        depends_on=task.depends_on,
                        result=task.result,
                        error_message="Task is not replayable after restart",
                        retry_count=task.retry_count,
                        replayable=False,
                    )
                    alert_manager.create_alert(
                        alert_type=AlertTypes.FAULT_ALERT,
                        severity=AlertSeverity.CRITICAL,
                        title=f"不可重放任务等待人工干预 ({task.task_id})",
                        message=f"请求 {task.request_id} 的任务缺少可重放快照，已转人工处理",
                        affected_resource=task.task_id,
                        context={
                            "tenant_id": task.tenant_id,
                            "user_id": task.user_id,
                            "thread_id": task.thread_id,
                            "previous_status": task.status,
                        },
                    )
        except Exception as e:
            logger.error("Startup task replay failed: %s", e)
            raise
```

注意：`incomplete_tasks` 兜底扫描仍然保留，目的是处理老数据或没有 `execution_snapshot` 的任务。

---

## 8. 测试建议

文件：`tests/unit/test_task_state_management.py`

至少补这两个测试。

### 8.1 快照字段测试

```python
def test_task_state_snapshot_fields() -> None:
    task = AgentTaskState(
        request_id="req-1",
        task_id="task-1",
        tenant_id="tenant-1",
        user_id="user-1",
        thread_id="thread-1",
        description="demo",
        replayable=True,
        idempotency_key="req-1:task-1",
        execution_snapshot={"request_query": "demo"},
    )

    assert task.replayable is True
    assert task.idempotency_key == "req-1:task-1"
    assert task.execution_snapshot["request_query"] == "demo"
```

### 8.2 无快照不可重放测试

```python
@pytest.mark.asyncio
async def test_replay_task_requires_snapshot() -> None:
    service = ChatService(
        model_client=MagicMock(),
        retriever=MagicMock(),
        trace=MagicMock(),
        memory=MagicMock(),
        _intervention_handler=MagicMock(),
        _alert_manager=MagicMock(),
        _metrics_collector=MagicMock(),
    )
    task = AgentTaskState(
        request_id="req-1",
        task_id="task-1",
        tenant_id="tenant-1",
        user_id="user-1",
        thread_id="thread-1",
        description="demo",
        execution_snapshot={},
    )

    with pytest.raises(ValueError, match="Task snapshot is empty"):
        await service.areplay_task_from_snapshot(task)
```

---

## 9. 完成后更新学习指南

文件：`项目指南/LEARNING_GUIDE.md`

如果只完成"发现并转人工"，保持：

```markdown
| 任务状态管理 / 状态机 | ⚠️ 状态已持久化，HITL 可恢复；运行中任务无法重启续跑 | 补启动扫描、执行快照、幂等重放和恢复测试 | 🔴 |
```

如果完成本文件所有改造，再改为：

```markdown
(已修复)| 任务状态管理 / 状态机 | ✅ 已实现状态持久化、执行快照、启动自动重放与 HITL 兜底 | 复盘幂等重放边界与失败兜底 | 🟡 |
```

---

## 10. 验证命令

```powershell
Set-Location "c:\git\rag-langchain\examples\enterprise_alert_agent"
$env:PYTHONPATH='.'
uv run --extra dev pytest tests/unit/test_task_state_management.py tests/unit/test_langgraph_fault_recovery.py -q
```

如果直接运行整个 `tests/unit/` 因 `_validate_secrets` 触发 `sys.exit(1)`，先按当前项目测试约定补齐测试环境变量，或只运行与状态机相关的单测。

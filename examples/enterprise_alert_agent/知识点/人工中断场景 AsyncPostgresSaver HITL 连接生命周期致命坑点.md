# AsyncPostgresSaver HITL 人工中断场景 连接生命周期致命坑点文档

## 1\. 业务背景

当前故障恢复工作流存在 **L4 人工干预节点（HITL \+ interrupt 断点暂停）** 能力：

- 工作流执行到 L4 节点触发 `interrupt()` → 流程暂停

- 等待用户传入 `resume` 决策（retry/skip）恢复流程

- 断点恢复强依赖 **LangGraph 原生 checkpoint 快照**（snapshot、next节点、interrupt上下文）

- 生产使用 `AsyncPostgresSaver` 将快照持久化到 PostgreSQL，替代内存级的 MemorySaver

**核心前提**：业务库 `agent_task_state` 仅保存业务状态，**无法替代 LangGraph checkpoint 快照**，不能用于恢复工作流断点。

## 2\. 原始错误代码（线上问题来源）

业务原有写法：**单次任务内通过 async with 临时创建 checkpointer**

```python
async with AsyncPostgresSaver.from_conn_string(
    settings.langgraph_checkpoint_dsn
) as checkpointer:
    await checkpointer.setup()
    workflow = FaultRecoveryWorkflow(
        analyzer=self.fault_analyzer,
        executor=FaultExecutor(
            _retry_handler,
            _fallback_handler,人工中断场景
            _rag_only_handler,
        ),
        max_retry_attempts=self.config_manager.get_task_max_retries(),
        checkpointer=checkpointer,
    )
    subtask_thread_id = f"{request_id}_{subtask.task_id}"
    outcome = await workflow.run(ctx, thread_id=subtask_thread_id)

```

## 3\. 致命问题现象

- 工作流走到 L4 `interrupt` 正常暂停，业务表状态保存成功

- **退出 async with 上下文后，PG 连接被自动关闭、checkpointer 实例销毁**

- 人工操作后调用 `resume` 恢复流程时：
        

    - 无可用的 PG 连接读取 checkpoint 快照

    - LangGraph 找不到暂停现场、找不到 interrupt 断点

    - **L4 中断丢失、无法 resume 恢复流程**

## 4\. 根因深度解析（核心坑点）

### 4\.1 AsyncPostgresSaver \+ async with 生命周期不支持 HITL 跨轮次恢复

`async with` 的生命周期 = **单次 ainvoke 执行周期**

- 进入上下文：创建 PG 连接、初始化 checkpointer

- 工作流暂停（interrupt）、ainvoke 返回

- 退出上下文：**强制关闭连接、销毁 checkpointer 实例**

而 **HITL 人工暂停场景是跨轮次调用**：

- 第一轮：启动流程 → 暂停（退出上下文，连接销毁）

- 第二轮：用户决策 resume → 需要再次读取 PG 快照

连接已断、实例已销毁，直接导致 **断点彻底丢失**。

### 4\.2 checkpointer\.setup\(\) 重复执行冗余问题

原代码每一次任务执行都调用 `await checkpointer.setup()`：

- 虽然建表逻辑幂等、不会报错

- 但每轮任务重复执行建表 SQL，造成不必要的数据库压力与性能损耗

- 错误设计：**setup 仅服务启动执行一次，不可放在业务流程内**

### 4\.3 关键认知误区（绝大多数人踩坑）

❌ 错误认知：**业务表有状态 = 可以恢复工作流**

✅ 正确认知：

- **业务表 agent\_task\_state**：只存业务字段，用于业务查询、任务展示，**不包含 LangGraph 运行栈、next 节点、interrupt 断点信息**

- **LangGraph Checkpoint 快照（PG）**：保存图运行现场、暂停位置、中断参数、完整 state，**唯一可用于 resume 恢复的数据源**

两者必须并行存在，缺一不可。

## 5\. 错误写法适用场景（仅纯一次性流程）

`async with + from_conn_string` 临时创建 checkpointer 的写法，**仅适合无中断、一次性跑完 END 的简单工作流**：

- 流程启动 → 完整跑完所有节点 → 直接结束

- 无人工暂停、无跨轮次 resume 需求

**绝对禁止用于 HITL 人工中断场景**。

## 6\. 生产级正确方案（适配 L4 断点恢复）

### 6\.1 核心改造原则

- checkpointer 实例 **全局常驻**，生命周期 = 服务生命周期

- `setup()` 服务启动全局执行一次

- 单次任务流程只复用全局 checkpointer，不重复创建、不销毁连接

- `thread_id` 全局唯一，绑定子任务 ID，保证中断/恢复同源

### 6\.2 正确代码实现

#### 步骤1：服务启动阶段（全局一次性初始化）

```python
# 全局常驻 PG 持久化 Checkpointer
global_pg_checkpointer = AsyncPostgresSaver.from_conn_string(
    settings.langgraph_checkpoint_dsn
)
# 仅启动时执行一次建表
await global_pg_checkpointer.setup()

```

#### 步骤2：业务任务执行阶段（复用全局实例）

```python
# 每次任务直接复用常驻 checkpointer，不新建、不关闭
workflow = FaultRecoveryWorkflow(
    analyzer=self.fault_analyzer,
    executor=FaultExecutor(
        _retry_handler,
        _fallback_handler,
        _rag_only_handler,
    ),
    max_retry_attempts=self.config_manager.get_task_max_retries(),
    checkpointer=global_pg_checkpointer,  # 全局常驻实例
)
# 唯一 thread_id 绑定子任务，保证中断恢复同源
subtask_thread_id = f"{request_id}_{subtask.task_id}"
# 启动/恢复流程共用同一个 thread_id、同一个 checkpointer
outcome = await workflow.run(ctx, thread_id=subtask_thread_id)

```

### 6\.3 恢复流程代码（完全不变）

```python
# 人工决策后，传入 resume + 同源 thread_id
outcome = await workflow.run(
    ctx,
    thread_id=subtask_thread_id,
    resume="retry"
)

```

## 7\. 正确生命周期流转（HITL 完整链路）

1. 服务启动：初始化全局 PG checkpointer、建表、长连接常驻

2. 任务启动：使用全局 checkpointer 运行工作流

3. L4 触发 interrupt：快照、interrupt 信息、next 节点全部持久化 PG

4. 流程暂停返回，**连接不关闭、实例不销毁、快照不丢失**

5. 人工操作后 resume：通过相同 thread\_id 读取 PG 快照，恢复断点继续执行

6. 流程最终走到 END，正常结束

## 8\. 最终总结（核心口诀）

- **带人工中断 HITL 的工作流，绝对不能用 async with 临时生命周期 checkpointer**

- **业务状态 ≠ LangGraph 快照，只有 PG checkpointer 能恢复断点**

- **checkpointer 必须全局常驻、长连接、启动仅一次 setup**

- **中断/恢复必须严格复用同一个 thread\_id**

> （注：部分内容可能由 AI 生成）

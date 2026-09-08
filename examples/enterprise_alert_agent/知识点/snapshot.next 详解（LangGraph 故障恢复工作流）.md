# snapshot\.next 详解（LangGraph 故障恢复工作流）

# snapshot\.next 详解（LangGraph 故障恢复工作流）

> 基于 FaultRecoveryWorkflow 代码，讲解 `snapshot.next` 的含义、场景、坑点与数据样例
>
>

## 核心根本规则

`snapshot.next` = **LangGraph 计划下一步马上要调度执行的节点名称列表**

- `snapshot.next` **非空**：还有节点待执行，流程未结束；绝大多数业务场景为 `interrupt()` 人工暂停；也存在异常中断残留场景。

- `snapshot.next = []` 空列表：没有待调度节点，工作流到达`END`，生命周期彻底结束。

> ⚠️重要区分
>
> - `state.trail`：记录**已经执行完毕**的节点历史轨迹。
>
> - `snapshot.next`：记录**还未执行、准备调度**的下一步节点。二者不要混淆。
>
>

> 业务代码片段参考
>
>

```python
snapshot = await graph.aget_state(config)
if snapshot.next:
    outcome = self._to_outcome(snapshot.values, status="awaiting_human", thread_id=thread_id)
    for task in snapshot.tasks or []:
        if task.interrupts:
            outcome.human_question = task.interrupts[0].value
            break
return self._to_outcome(snapshot.values, status="completed", thread_id=thread_id)
```

> 现有代码约定：`snapshot.next`不为空就标记状态为`awaiting_human`，这是业务层假设，**不是 LangGraph 原生强制规则**。
>
>

## 场景 1：snapshot\.next 不为空 — L4 interrupt 人工暂停（正常业务场景）

### 发生条件

工作流执行到 `_l4_human_intervention` 节点内部执行 `interrupt(question)`：

```python
async def _l4_human_intervention(self, state: FaultRecoveryState):
    human_decision = interrupt("全部自动策略失败，请选择：retry / skip")
    # 下面代码暂停，等待外部 resume 输入
```

执行到`interrupt()`时：

1. 当前 L4 节点**没有跑完，半路挂起**，不会返回增量 state，不会触发条件边路由。

2. LangGraph 停止调度，`ainvoke()`直接返回。

3. checkpointer 持久保存快照：完整 state \+ 待执行节点标记。

### 快照数据示例

```python
# snapshot.next：不为空，下一步待执行 l4_human_intervention
snapshot.next = ["l4_human_intervention"]

# state.values
{
    "request_id": "req‑001",
    "fault_context": {"tool_name":"query_db", "error":"timeout"},
    "level": 4,
    "attempts": 2,
    "trail": [
        "analyze_fault:detect_timeout",
        "l1_retry:exec failed",
        "l2_adjust:exec failed",
        "l3_rag_only:exec failed"
    ],
    "resolved": False
}

# interrupt提问保存在tasks.interrupts
snapshot.tasks[0].interrupts[0].value = "全部自动策略失败，请选择：retry / skip"
```

### 业务含义

1. `trail` 表明：`analyze_fault / l1_retry / l2_adjust / l3_rag_only` 已经全部执行完毕。

2. `snapshot.next = ["l4_human_intervention"]`：**不是已经跑完 L4，而是 L4 节点被 interrupt 挂起，后续需要继续执行该节点**。

3. 流程并未结束，处于人工暂停；上层业务保存`thread_id`，展示提问，等待用户输入 resume 恢复流程。

4. run 方法返回：`status="awaiting_human"`。

### 恢复之后变化

调用 `await graph.ainvoke(Command(resume="retry"), config)`

1. 加载快照，将`retry`送入 interrupt 断点，L4 节点继续执行完毕。

2. 节点返回增量 state，触发条件边路由，继续调度后续节点（示例中调度`l1_retry`）。

3. 完整流转直到路由返回`END`。

4. 流程结束后：`snapshot.next = []`。

## 场景 2：snapshot\.next 不为空，但没有 interrupts（异常脏快照）

### 发生条件

上一次`ainvoke()`执行中途异常终止：进程 kill、未捕获异常、外部强制取消任务。
节点未完整跑完，checkpointer 保存了残留快照。

示例快照：

```python
snapshot.next = ["l1_retry"]
snapshot.tasks 无任何 interrupts
```

含义：流程上次执行被打断，下一步本应执行`l1_retry`，**不存在人工暂停，不需要用户输入**。

### 现有代码局限

原代码逻辑：只要`snapshot.next`非空，直接标记`status="awaiting_human"`。
此时会出现错误：

- outcome\.status = `"awaiting_human"`

- `outcome.human_question = None`

> ✅严谨判断逻辑（生产环境建议补充）
>
>

```python
has_interrupt = any(t.interrupts for t in snapshot.tasks or [])
if snapshot.next and has_interrupt:
    status = "awaiting_human"
else:
    status = "completed"
```

## 场景 3：snapshot\.next = \[\] 空列表，流程到达 END，生命周期彻底结束

### 触发条件

路由函数`_route`返回`END`常量，代表工作流结束。
触发路径举例：

1. L1 重试成功，`resolved=True`，路由返回 END。

2. L4 人工选择 skip，返回`resolved=False`，路由返回 END。

### 快照样例

```python
snapshot.next = []
snapshot.values = {
    "request_id": "req‑001",
    "fault_context": {"tool_name":"query_db", "error":"timeout"},
    "level": 1,
    "attempts": 2,
    "trail": [
        "analyze_fault:detect_timeout",
        "l1_retry:exec failed",
        "l2_adjust:exec failed",
        "l3_rag_only:exec failed",
        "l4:human_decision=retry",
        "l1_retry:success after human resume"
    ],
    "resolved": True,
    "output": "db query result ok"
}
```

### 重要约束

1. `snapshot.next = []`，到达 END，**该 thread\_id 对应的工作流彻底结束**。

2. 即使复用同一个`thread_id`调用`ainvoke(Command(resume=...))`，不会再执行任何节点。

3. 如果需要再次执行故障恢复，**使用全新 thread\_id，传入新的 initial\_state 重新启动**。

## 完整生命周期中 snapshot\.next 变化时序

1. `run(resume=None)` 全新启动流程

    - 执行：`analyze_fault → l1_retry → l2_adjust → l3_rag_only`

    - 进入`l4_human_intervention`执行`interrupt()`触发暂停，`ainvoke`返回

    - `aget_state()` → `snapshot.next = ["l4_human_intervention"]`（非空，带 interrupt）

    - 返回 outcome `status="awaiting_human"`

2. 用户输入`retry`，调用`run(resume="retry", thread_id="th‑001")`

    - `Command(resume="retry")`恢复断点，L4 节点跑完，返回增量

    - 条件边路由调度`l1_retry`，执行业务重试，重试成功

    - `_route`识别`resolved=True`返回`END`

    - `aget_state()` → `snapshot.next = []`

    - 返回 outcome `status="completed"`，流程结束

## 关键误区总结

1. ❌误区：`snapshot.next`保存已经跑完的节点

> ✅正确：`state.trail`保存历史执行轨迹；`snapshot.next`保存待调度未执行节点。
>
>

2. ❌误区：`snapshot.next`非空就一定需要人工输入

> ✅正确：只有同时满足 `snapshot.next非空` **并且** `tasks存在interrupts`，才是真正人工暂停；next 非空也可能是进程异常中断残留脏快照。
>
>

3. ❌误区：到达 END 之后，还可以用 Command \(resume\) 继续恢复

> ✅正确：END 代表工作流生命周期结束，不能 resume 恢复，需要新 thread\_id 重新启动。
>
>

## 一句话速记

- **snapshot\.next 非空**：还有节点待执行。正常业务场景为 L4 人工 interrupt 暂停；异常场景为上次执行中断残留脏快照。

- **snapshot\.next = \[\]**：到达 END 终点，整个故障恢复工作流结束，不可再 resume 恢复。

---

你可以直接全选复制全部文本，粘贴保存为 `snapshot_next_knowledge.md` 文件到本地。

> （注：部分内容可能由 AI 生成）

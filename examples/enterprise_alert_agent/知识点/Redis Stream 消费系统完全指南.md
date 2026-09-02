# Redis Stream 消费系统完全指南

## 📋 一句话总结

**Redis Stream + Consumer Group** 实现异步消息队列：生产者发送 → Stream 存储 → Consumer 持续消费 → XACK 标记 → DLQ 异常恢复。

---

## 🎬 核心流程（从生到死）

```
用户上传文档
    ↓
POST /ingest/text
    ↓
XADD → Redis Stream  (task_id: "1725278400000-0")
    ↓
【消息进入 Stream，等待消费】
    ↓
RedisStreamWorker 后台协程
    ↓
XREADGROUP 获取新消息 (">")
    ↓
_handler() 处理
    ├─ ✅ 成功 → XACK
    └─ ❌ 异常 → DLQ + XACK
    ↓
【消息被标记为已处理，永不再出现】
```

---

## ❓ 两个核心问题

### **Q1：XACK 后 XREADGROUP 还会拿到消息吗？**

### ✅ 答案：**绝对不会！XACK = 从待处理列表永久移除**

```python
【时间线演示】

T1 (0.1秒)  生产者
            task_id = await redis.xadd("tasks:ingest", {"payload": "..."})
            # task_id = "1725278400000-0"

T2 (0.2秒)  消费者第一次读取
            messages = await redis.xreadgroup(
                "ingest-workers", 
                "worker-node1",
                {"tasks:ingest": ">"},  # ← ">" 表示只读新消息
            )
            # ✅ 返回：[("1725278400000-0", {"payload": "..."})]
            # 消息进入 PendingList（待处理列表）

T3 (0.3秒)  处理消息
            await handle_ingest_task(payload)
            # 处理成功

T4 (0.4秒)  确认消息
            await redis.xack("tasks:ingest", "ingest-workers", "1725278400000-0")
            # 🔑【关键】消息从 PendingList 移除

T5 (0.5秒)  消费者第二次读取
            messages = await redis.xreadgroup(...)
            # ❌ 返回：[] (空！)
            # 即使调用 1000 次也不会有 "1725278400000-0"

T6 (1.0秒)  生产者发送新消息
            task_id2 = await redis.xadd(...)
            # task_id2 = "1725278401000-0"  (新 ID)

T7 (1.1秒)  消费者继续读取
            messages = await redis.xreadgroup(...)
            # ✅ 返回：[("1725278401000-0", {...})]  (只有新消息)
```

**核心结论**：
```
Redis State:
┌─────────────────────────────────────┐
│ Stream "tasks:ingest"               │
├─────────────────────────────────────┤
│ msg-1 (已XACK, 不在PendingList)     │  ← Stream中仍有这条消息
│ msg-2 (已XACK, 不在PendingList)     │
│ msg-3 (未XACK, 在PendingList中)     │  ← XREADGROUP 只会返回这条
│ msg-4 (新消息, 不在PendingList)     │  ← 和这条
└─────────────────────────────────────┘

XREADGROUP ">" 返回: [msg-3, msg-4]  (从上次消费位置之后的所有新消息)
```

---

### **Q2：消费协程一直运行直到 close() 吗？**

### ✅ 答案：**是的！协程从启动持续运行，直到调用 close()**

```python
【应用生命周期】

应用启动
    ↓
await stream_worker.startup()
    ├─ XGROUP CREATE （创建消费者组）
    └─ asyncio.create_task(self._consume())
        ✅ 协程启动，开始无限循环

        async def _consume(self):
            while not self._stop_event.is_set():  # ← 检查停止标志
                messages = await redis.xreadgroup(..., block=1000)
                # 阻塞读取，无消息时等待1秒
                
                for msg_id, fields in messages:
                    await _handle_message(msg_id, fields)
                    # 处理消息，XACK，异常则DLQ
                
                # 循环继续 → 回到 while 条件检查
                # （如果没有设置 stop_event，会一直循环）

应用运行中 (可能持续数小时/天)
    ✅ 协程持续消费消息...

应用收到关闭信号 (SIGTERM)
    ↓
await stream_worker.close()
    ├─ self._stop_event.set()          ← 设置停止标志
    └─ await self._worker_task         ← 等待协程完成
        ├─ 当前 xreadgroup 的 block=1000 等待中...
        ├─ 等待超时 → 返回 (无新消息)
        ├─ 检查 while not self._stop_event.is_set()
        ├─ 条件为 False (stop_event 已设置)
        └─ 退出 while → 协程完成 ✅

应用完全关闭
```

---

## 🔧 代码实现细节

### 启动阶段 (main.py)
```python
@app.on_event("startup")
async def startup_checks() -> None:
    from redis import asyncio as redis_asyncio
    
    # 1. 初始化业务处理函数
    async def handle_ingest_task(payload: dict[str, Any]) -> None:
        """处理文档入库任务"""
        logger.info("Processing: %s", payload)
        # await ingest_service.process(payload)
    
    # 2. 创建 Redis 客户端
    redis_client = redis_asyncio.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password,
        decode_responses=True,
    )
    
    # 3. 创建并启动 Worker
    stream_worker = RedisStreamWorker(
        redis_client=redis_client,
        stream_key="tasks:ingest",
        group_name="ingest-workers",
        handler=handle_ingest_task,
    )
    await stream_worker.startup()  # ← 【启动协程】
    app.state.stream_worker = stream_worker
```

### 消费阶段 (redis_stream.py)
```python
async def startup(self) -> None:
    # 创建消费者组（如果已存在则忽略）
    try:
        await self._redis.xgroup_create(
            name="tasks:ingest",
            groupname="ingest-workers",
            id="0",           # 从头开始消费
            mkstream=True,    # 如果 stream 不存在则创建
        )
    except RedisError as e:
        if "BUSYGROUP" not in str(e):  # 容错
            raise
    
    # 启动消费协程
    self._worker_task = asyncio.create_task(self._consume())


async def _consume(self) -> None:
    while not self._stop_event.is_set():  # ← 循环条件
        # 获取新消息（">" = 只读组内未消费过的）
        messages = await self._redis.xreadgroup(
            self._group_name,           # "ingest-workers"
            self._consumer_name,        # "worker-node1"
            {self._stream_key: ">"},    # ← 关键
            count=10,                   # 每次最多10条
            block=1000,                 # 阻塞1秒
        )
        
        for _, entries in messages:
            for message_id, fields in entries:
                await self._handle_message(message_id, fields)


async def _handle_message(self, message_id: str, fields: dict[str, str]) -> None:
    try:
        payload = json.loads(fields["payload"])
        await self._handler(payload)           # ← 业务处理
        
        # ✅ 成功 → XACK
        await self._redis.xack(
            self._stream_key,
            self._group_name,
            message_id
        )
    except Exception as e:
        logger.exception("Failed: %s", message_id)
        
        # ❌ 失败 → DLQ（死信队列，自动重试）
        await dead_letter_queue.add(
            task_id=message_id,
            payload={"fields": fields},
            failure_reason=str(e)
        )
        
        # 重要：无论成功/失败都要 XACK
        # 否则消息会一直在 PendingList 中
        await self._redis.xack(
            self._stream_key,
            self._group_name,
            message_id
        )
```

### 关闭阶段 (main.py)
```python
@app.on_event("shutdown")
async def shutdown_resources() -> None:
    logger.info("Application shutdown started")
    
    # 关闭 Redis Stream worker
    stream_worker = getattr(app.state, "stream_worker", None)
    if stream_worker is not None:
        try:
            await asyncio.wait_for(
                stream_worker.close(),      # ← 【关闭协程】
                timeout=30,                 # 最多等待30秒
            )
        except TimeoutError:
            logger.error("Stream worker timeout")
    
    logger.info("Application shutdown completed")


async def close(self) -> None:
    self._stop_event.set()          # 设置停止标志
    if self._worker_task is not None:
        await self._worker_task     # 等待协程完成
```

---

## 💎 关键概念映射表

| Redis 概念 | 说明 | 代码 |
|-----------|------|------|
| **Stream Key** | 消息流 (有序队列) | `"tasks:ingest"` |
| **Message ID** | 时间戳-序列号 | `"1725278400000-0"` |
| **Consumer Group** | 消费者组 (共享进度) | `"ingest-workers"` |
| **Consumer** | 消费者 (处理消息) | `"worker-node1"` |
| **XADD** | 生产消息 | `xadd("tasks:ingest", {...})` |
| **XREADGROUP** | 消费消息 | `xreadgroup(..., {stream: ">"})` |
| **">"** | 只读新消息 (该组未消费过的) | `{self._stream_key: ">"}` |
| **XACK** | 确认处理完成 | `xack(stream, group, msg_id)` |
| **PendingList** | 待处理列表 | 未 XACK 的消息 |
| **DLQ** | 死信队列 (异常处理) | `dead_letter_queue.add(...)` |

---

## ⚠️ 常见陷阱

### 陷阱1：处理异常时忘记 XACK
```python
❌ 错误：
try:
    await handler(payload)
except Exception:
    pass  # ❌ 没有 XACK

结果：消息永远在 PendingList，无法继续处理新消息

✅ 正确：
try:
    await handler(payload)
except Exception as e:
    await dlq.add(...)
finally:
    await redis.xack(...)  # ✅ 无论成功/失败都要 XACK
```

### 陷阱2：XREADGROUP 无限阻塞
```python
❌ 错误：
messages = await redis.xreadgroup(..., block=-1)  # ❌ 无限等待

结果：关闭应用时协程卡死，无法优雅退出

✅ 正确：
messages = await redis.xreadgroup(..., block=1000)  # ✅ 1秒超时
# 好处：每秒检查一次 stop_event，可以及时退出
```

### 陷阱3：关闭时不等待协程
```python
❌ 错误：
stream_worker._stop_event.set()  # ❌ 直接返回

结果：协程被强制中断，可能丢失消息

✅ 正确：
await asyncio.wait_for(
    stream_worker.close(),  # ✅ 等待协程完成
    timeout=30,
)
```

---

## 🎯 生产环境增强建议

### 1. 实现生产端（当前缺失）
```python
# 在 POST /ingest/text 中添加
task_id = await redis.xadd(
    "tasks:ingest",
    {"payload": json.dumps(payload, ensure_ascii=False)}
)
return {"status": "queued", "task_id": task_id}
```

### 2. 业务处理函数实现
```python
# 当前是 TODO，需要实现真正的处理逻辑
async def handle_ingest_task(payload: dict[str, Any]) -> None:
    req = IngestTextRequest(
        content=payload["content"],
        source_id=payload["source_id"],
        metadata=payload.get("metadata", {}),
    )
    result = ingest_service.ingest_text(req)
    logger.info("✅ Ingest completed: %s", result)

## 📊 状态转换表

```
消息生命周期：

【1】生产阶段
    XADD("tasks:ingest", payload)
    ↓
    消息进入 Stream: [msg-1, msg-2, msg-3]
    PendingList: []

【2】读取阶段
    XREADGROUP "ingest-workers" "worker-1" ">"
    ↓
    消息被分配给 worker-1
    PendingList: [(msg-1, worker-1, idle=0ms)]

【3】处理阶段
    handler(payload)
    ├─ ✅ 成功 → 进入确认阶段
    └─ ❌ 异常 → 进入 DLQ，仍需要 XACK

【4】确认阶段
    XACK("tasks:ingest", "ingest-workers", msg-1)
    ↓
    PendingList: []  (msg-1 被移除)
    ✅ 消息永不再出现

【5】下次读取
    XREADGROUP 返回新消息 (msg-2, msg-3, msg-4, ...)
    (绝对不会再有 msg-1)
```

---

## 🔍 快速排查指南

| 问题 | 排查步骤 | 解决方案 |
|------|---------|---------|
| 消息没被消费 | ① 检查 Worker 是否启动 | `logger.info("Redis Stream worker started")` |
| | ② 检查消息是否在 Stream 中 | `XLEN tasks:ingest > 0` |
| | ③ 检查消费者组是否存在 | `XINFO GROUPS tasks:ingest` |
| 消息重复处理 | ① 检查是否 XACK | 代码中必须有 `xack()` 调用 |
| | ② 检查是否有多个 Worker 组 | 确保同一组 |
| 应用无法关闭 | ① 检查 block 超时 | 应该是 1000ms，不是 -1 |
| | ② 检查是否等待协程 | `await wait_for(..., timeout=30)` |
| DLQ 堆积 | ① 查看重试次数 | `XPENDING tasks:ingest ingest-workers` |
| | ② 检查处理函数逻辑 | 业务错误需要修复 |

---

## 📝 总结：三个必须知道的事

1. **XACK 永久移除** ← XACK 后 XREADGROUP 不会再返回该消息，即使调用 1000 次
2. **协程一直运行** ← 从 startup() 到 close()，协程在后台持续消费消息
3. **无论成功/失败都要 XACK** ← 否则消息卡在 PendingList，DLQ 无法继续处理


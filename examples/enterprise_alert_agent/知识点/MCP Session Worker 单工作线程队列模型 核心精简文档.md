# MCP Session Worker 单工作线程队列模型 核心精简文档

## 1\. 核心设计思想（最关键）

**背景问题**：MCP 会话连接不支持并发读写，多协程同时调用会报文错乱、连接崩溃。

**解决方案**：**单 Worker 协程 \+ 异步队列串行消费模型**

**核心原则**：

- 全局仅 **一个后台 Worker 协程** 操作 MCP Session

- 所有外部工具调用不直接执行，全部投递队列排队

- 单线程串行消费，天然避免并发竞态、连接错乱

- 重连、故障恢复、资源释放全部收敛在 Worker 内部，线程归属统一

## 2\. 整体运行机制（一句话）

Worker 启动后进入 **while True 无限循环**，通过 `await queue.get()` 阻塞等待任务：

- 队列空 → 协程挂起、不耗 CPU、常驻等待

- 有新任务 → 唤醒消费、执行工具调用

- 收到哨兵 None → 优雅退出、关闭连接

## 3\. 核心结构总览

- **call\_tool\(\)**：对外公开入口，熔断校验、投递队列、等待结果

- **\_run\(\)**：Worker 主循环（唯一消费线程）

- **\_handle\_call\(\)**：内部执行调用，支持超时、自动重连、重试

- **close\(\)**：优雅停机，发哨兵 \+ 等待 Worker 退出

---

## 4\. 初始化 \+ 启动 Worker 代码（带完整注释）

作用：保证全局只启动一个 Worker，建立 MCP 连接、握手就绪

```python
# 初始化 & 启动常驻 Worker
if self._worker_task is not None:
    return self._initialized  # 防止重复启动 Worker

self._queue = asyncio.Queue()
# 握手Future：用于外部等待连接初始化完成
ready: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
# 启动唯一后台Worker协程
self._worker_task = asyncio.create_task(self._run(ready), name="mcp-session-worker")
# 阻塞等待：直到Worker完成建连、告知就绪
return await ready
```

## 5\. Worker 主循环 \_run（核心常驻逻辑）

```python
async def _run(self, ready: asyncio.Future[bool]) -> None:
    # 1. Worker启动第一件事：建立MCP连接
    ok = await self._do_connect()
    # 通知外部初始化结果（唤醒外层 await ready）
    if not ready.done():
        ready.set_result(ok)

    # 常驻无限循环：持续消费队列
    while True:
        # 关键：队列为空则阻塞挂起，不退出！
        # 只有队列有数据才唤醒消费
        request = await self._queue.get()

        # 优雅退出唯一条件：收到哨兵 None
        if request is None:
            break

        # 串行执行MCP工具调用
        response = await self._handle_call(request.tool_name, request.tool_args)
        
        # 将结果回填请求Future，唤醒外部调用方
        if not request.future.done():
            request.future.set_result(response)
    
    # 退出循环后，主动关闭MCP连接、释放资源
    await self._do_close()
```

### 关键知识点（必记）

- **队列为空 ≠ 退出**：`await queue.get()` 空队列只会阻塞等待，不会返回 None

- **只有手动 put\(None\) 才会退出**（哨兵机制）

- 每 get\(\) 一次，任务**直接从队列移除**，不会重复消费

---

## 6\. 内部调用逻辑 \_handle\_call（重试 \+ 重连 \+ 超时）

仅 Worker 内部调用，保证所有连接操作单线程、无竞态

```python
async def _handle_call(
    self, tool_name: str, tool_args: dict[str, Any], *, max_attempts: int = 2
) -> dict[str, Any]:
    last_error: Exception | None = None
    # 自动重试机制
    for attempt in range(1, max_attempts + 1):
        # 会话失效：先关闭、再重连
        if self._session is None or not self._initialized:
            await self._do_close()
            if not await self._do_connect():
                last_error = RuntimeError("MCP session not initialized")
                break

        try:
            # 超时保护：防止MCP卡死拖垮Worker
            result = await asyncio.wait_for(
                self._session.call_tool(name=tool_name, arguments=tool_args),
                timeout=settings.mcp_call_timeout_seconds,
            )
            # 成功直接返回
            return {"status": "success", "content": result.content}
        
        except Exception as e:
            # 失败标记会话失效，进入重试
            last_error = e
            self._initialized = False
            if attempt < max_attempts:
                await asyncio.sleep(0.2 * attempt) # 线性退避重试

    # 全部重试失败，返回统一错误结构
    return {"status": "error", "message": str(last_error)}
```

---

## 7\. 对外入口 call\_tool（熔断 \+ 队列投递）

所有业务层调用的入口，**不阻塞、不直接执行MCP**，只做消息投递

```python
async def call_tool(self, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
    # 1. 熔断器前置拦截（防雪崩）
    try:
        self._circuit_breaker.before_call()
    except CircuitOpenError:
        return {"status": "error", "error_code": "circuit_open"}

    # 2. 校验Worker是否正常运行
    if self._queue is None or self._worker_task is None:
        self._circuit_breaker.record_failure()
        return {"status": "error", "error_code": "not_initialized"}

    # 3. 创建Future：用于接收Worker返回的结果
    future = asyncio.get_running_loop().create_future()
    # 投递任务入队
    await self._queue.put(_CallRequest(tool_name, tool_args, future))

    # 阻塞等待Worker处理完成、回填结果
    response = await future

    # 4. 根据结果更新熔断器状态
    if response.get("status") == "success":
        self._circuit_breaker.record_success()
    else:
        self._circuit_breaker.record_failure()

    return response
```

---

## 8\. 优雅关闭逻辑（重点解析 await worker\_task）

```python
async def close(self):
    if self._worker_task is None:
        return
    assert self._queue is not None

    # 1. 发送哨兵信号：通知Worker退出循环
    await self._queue.put(None)
    # 2. 【核心】等待Worker完整跑完、执行_do_close、彻底退出
    await self._worker_task
    # 3. 清空引用
    self._worker_task = None
    self._queue = None
```

### await self\.\_worker\_task 核心作用

- **不会主动停止Worker**

- 作用是：**阻塞等待Worker协程彻底执行完毕**

- 必须先 put\(None\) 让Worker跳出循环，await 才会放行

- 保证关闭前所有收尾逻辑（关闭连接、释放栈）全部完成，杜绝资源泄漏

---

## 9\. 核心队列机制（你提问的重点汇总）

### 9\.1 为什么 Worker 可以常驻不退出？

- `while True` 无限循环

- `await queue.get()` 空队列阻塞挂起，不退出、不销毁协程

- 只有哨兵 None、异常崩溃、手动 cancel 三种情况会停止

### 9\.2 队列为空、拿到 None 是两回事

- 队列为空 → get\(\)**阻塞等待，不返回**

- 队列收到 None → get\(\) 返回 None → break 退出循环

### 9\.3 队列消费机制

- `queue.get()` = **取出 \+ 彻底删除任务**

- 任务只会被消费一次，不会重复消费

---

## 10\. 整套模型优势

1. **线程安全**：唯一 Worker 操作 Session，无并发竞态

2. **高可用**：内部自动重连、重试、超时保护

3. **防雪崩**：外层熔断器拦截故障流量

4. **优雅启停**：哨兵机制收尾，无资源泄漏

5. **解耦**：业务只管投递队列，不用管底层连接细节

---

## 11\. 现存风险点（工程注意）

- 单次任务卡死会阻塞整个队列（单线程串行固有特性）

- 当前 while 循环无全局 try\-except，异常会导致 Worker 彻底挂掉

- 重试不区分「业务错误/网络错误」，无效重试浪费资源

> （注：部分内容可能由 AI 生成）

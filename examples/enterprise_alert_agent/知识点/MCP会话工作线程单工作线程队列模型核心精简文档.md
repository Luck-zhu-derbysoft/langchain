# MCP会话工作线程单工作线程队列模型核心精简文档

# MCP Session Worker 单工作线程队列模型 核心精简文档（适配你这份真实 RemoteMCPClient 源码）

> 适配你贴出来完整源码，修正之前文档和代码不一致之处，保留知识点、带源码注释、可直接复制保存为 `.md`
> 
> 

## 1\. 核心设计思想

**背景**：MCP streamable http 会话不支持多协程并发读写，并发调用会导致报文错乱、连接损坏。
**方案**：**单 Worker 协程 \+ asyncio\.Queue 串行消费模型**

- 全局只启动一个后台 Worker 协程，所有 MCP 会话操作（建连、工具调用、关闭）全部收敛到这个 Worker；

- 外部所有 `call_tool` 不直接操作 MCP Session，封装成请求对象投递队列排队；

- 单协程串行执行，天然规避并发竞态；

- 连接失效自动关闭旧会话、自动重连 \+ 重试；

- 熔断器保护，MCP 服务持续故障时快速失败，防止雪崩。

> 注释掉的 `_call_lock`：早期想用锁串行，现在改用单 Worker 队列方案替代。
> 
> 

## 2\. 数据结构定义：\_CallRequest

```python
@dataclass
class _CallRequest:
    tool_name: str
    tool_args: dict[str, Any]
    # future：用来把worker内部执行结果回传给外部调用协程
    # compare=False：dataclass比较时忽略future，future不能参与相等判断
    future: "asyncio.Future[dict[str, Any]]" = field(compare=False)
```

- 每个外部工具调用封装成 `_CallRequest`，丢进队列；

- `future` 是**双向通知载体**：外部 await future 阻塞等待；worker 拿到请求执行完成后，`set_result` 唤醒外部。

## 3\. RemoteMCPClient 成员变量一览

```python
class RemoteMCPClient:
    def __init__(self) -> None:
        self._tools_meta: list[dict[str, Any]] = [] # MCP服务工具列表，转成function格式，给agent使用
        self._initialized = False # 标记当前session是否可用
        self._session: ClientSession | None = None # MCP ClientSession实例
        self._exit_stack: AsyncExitStack | None = None # 统一管理http client、stream、session的异步资源栈
        self._queue: asyncio.Queue[_CallRequest | None] | None = None # 任务队列，支持放入_CallRequest或者哨兵None
        self._worker_task: asyncio.Task[None] | None = None # 唯一后台worker协程任务
        self._circuit_breaker = CircuitBreaker(...) # 熔断器，MCP故障达到阈值直接拒绝新请求
```

`AsyncExitStack`：批量管理多个异步上下文（http\_client /streamable\_http/ ClientSession），一次性 `aclose()` 释放整套资源。

## 4\. initialize \(\)：启动 Worker，初始化 MCP 连接

```python
async def initialize(self) -> bool:
    if self._worker_task is not None:
        return self._initialized # 防止重复启动worker，幂等
    self._queue = asyncio.Queue()
    # ready future：外部等待worker完成MCP建连初始化
    ready: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    # 创建后台worker协程，执行_run主循环
    self._worker_task = asyncio.create_task(self._run(ready))
    # 阻塞，直到worker完成_do_connect并设置ready结果
    return await ready
```

流程：

1. 防止重复创建 worker；

2. 创建队列；

3. 创建 ready Future；

4. 启动 `_run` worker 协程；

5. `await ready` 阻塞，等待 worker 完成 MCP 握手。

## 5\. \_run \(ready\)：Worker 主循环（核心）

```python
async def _run(self, ready: asyncio.Future[bool]) -> None:
    try:
        # worker第一件事：建立MCP连接
        connected = await self._do_connect()
        # 通知initialize，建连结果
        if not ready.done():
            ready.set_result(connected)
        if self._queue is None:
            return
        # 常驻循环
        while True:
            # 阻塞等待队列任务：队列为空就挂起，不退出
            request = await self._queue.get()
            # 哨兵 None：收到关闭信号，跳出循环
            if request is None:
                break
            # 消费请求，执行MCP工具调用
            response = await self._handle_request(request.tool_name, request.tool_args)
            # 回填结果，唤醒外部call_tool的await future
            if request.future and not request.future.done():
                request.future.set_result(response)
    finally:
        # 无论正常退出还是异常，都执行资源清理
        await self._shutdown_worker_resources()
        self._worker_task = None
```

重点：

1. `while True` \+ `await self._queue.get()`：**队列为空只阻塞，不会退出**；只有队列收到 `None` 哨兵才 break；

2. `queue.get()` 取出元素后，元素自动从队列移除，任务只会被消费一次；

3. 外层 `try-finally`：worker 无论怎么退出，都会执行资源释放，重置 worker 引用；

4. 所有会话操作全部在这个协程内部执行，天然无并发竞态。

## 6\. \_do\_connect \(\)：建立 MCP streamable http 会话

```python
async def _do_connect(self) -> bool:
    try:
        stack = AsyncExitStack()
        # 创建http client，携带鉴权header
        http_client = await stack.enter_async_context(
            create_mcp_http_client(headers=headers, timeout=httpx.Timeout(settings.mcp_connect_timeout_seconds))
        )
        # streamable http 双向流
        read, write, _ = await stack.enter_async_context(
            streamable_http_client(url=settings.mcp_service_url, http_client=http_client)
        )
        # 创建MCP ClientSession
        session = await stack.enter_async_context(ClientSession(read_stream=read, write_stream=write))
        # MCP握手初始化
        await asyncio.wait_for(session.initialize(), timeout=settings.mcp_connect_timeout_seconds)
        # 获取MCP服务工具列表，转成OpenAI function格式
        tools_results = await session.list_tools()
        self._tools_meta = [ ... ]
        self._initialized = True
        self._session = session
        self._exit_stack = stack
        return True
    except Exception:
        logger.exception("MCP client initialization failed")
        return False
```

- `AsyncExitStack` 统一托管多层异步上下文；

- 连接成功后，缓存 `session`、`exit_stack`、工具元数据；

- 失败返回 False，上层 ready future 拿到 false，初始化失败。

## 7\. \_handle\_request：工具调用 \+ 自动重试 \+ 会话销毁（Worker 内部执行）

```python
async def _handle_request(
    self, tool_name: str, tool_args: dict[str, Any], *, max_attempts: int = 2
) -> dict[str, Any]:
    for attempt in range(1, max_attempts + 1):
        try:
            # session不存在，自动触发重连
            if self._session is None or self._exit_stack is None:
                conn = await self._do_connect()
                if not conn:
                    raise RuntimeError("Failed to connect to MCP server")
            logger.info("MCP call start: tool=%s attempt=%d", tool_name, attempt)
            # 带超时保护调用MCP工具
            result = await asyncio.wait_for(
                self._session.call_tool(name=tool_name, arguments=tool_args),
                timeout=settings.mcp_call_timeout_seconds,
            )
            # 对返回内容做结构化封装，统一返回格式
            content = result.content
            if isinstance(content, list) and content:
                texts = [getattr(item, "text", str(item)) for item in content if item is not None]
                return {
                    "status": "success",
                    "data": [{"result": "\n".join(texts)}],
                    "row_count": len(content),
                    "error_code": "",
                    "message": "ok",
                }
            return {"status": "success", "data": [], "row_count": 0, "error_code": "", "message": "ok"}
        except Exception as e:
            # 调用异常：标记会话失效，主动关闭旧连接
            self._initialized = False
            logger.warning("MCP call failed (attempt %d/%d): tool=%s err=%s", attempt, max_attempts, tool_name, e)
            if self._session is not None:
                try:
                    if self._exit_stack is not None:
                        await self._exit_stack.aclose()
                except Exception as e:
                    logger.warning("Error closing MCP session: %s", e)
                finally:
                    self._session = None
                    self._exit_stack = None
            # 未到最大重试次数，休眠退避重试
            if attempt < max_attempts:
                await asyncio.sleep(0.2 * attempt)
    # 全部重试耗尽，返回错误结构
    return {"status": "error", "tool_name": tool_name, "tool_args": tool_args}
```

核心逻辑：

1. 会话为空 → 自动调用 `_do_connect` 重建会话；

2. `asyncio.wait_for` 防止 MCP 服务卡死，阻塞整个 worker；

3. 异常时：标记 `_initialized=False`，关闭 `exit_stack`，置空 session，下一次请求会触发重连；

4. 最多 `max_attempts` 次重试，线性退避；

5. 统一返回结构化 dict，方便上层解析。

## 8\. call\_tool：对外公开入口（业务层调用）

```python
async def call_tool(self, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
    try:
        self._circuit_breaker.before_call() # 熔断器前置校验，如果打开直接抛CircuitOpenError
    except CircuitOpenError:
        logger.warning("MCP circuit open, blocking tool call: %s", tool_name)
        return {
            "status": "error",
            "data": [],
            "row_count": 0,
            "error_code": "circuit_open",
            "message": "MCP circuit is open, tool call blocked",
        }
    # 检查worker/队列是否正常
    if self._queue is None or self._worker_task is None:
        self._circuit_breaker.record_failure()
        return {
            "status": "error",
            "data": [],
            "row_count": 0,
            "error_code": "not_initialized",
            "message": "MCP service is not initialized",
        }
    # 创建future，用于接收worker返回结果
    function: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    # 投递任务进队列
    await self._queue.put(_CallRequest(tool_name, tool_args, function))
    # 阻塞等待worker处理完成，回填future结果
    result = await function
    # 根据调用结果更新熔断器状态
    if result.get("status") == "success":
        self._circuit_breaker.record_success()
    else:
        self._circuit_breaker.record_failure()
    return result
```

职责：

- 熔断器前置拦截；

- 校验 worker 状态；

- 封装请求对象投递队列；

- `await future` 等待 worker 处理结果；

- 回调熔断器成功 / 失败统计。

## 9\. close \(\) 优雅停机（哨兵机制）

```python
async def close(self) -> None:
    if self._queue is None or self._worker_task is None:
        return
    # 放入哨兵None，worker消费到None就跳出while循环
    await self._queue.put(None)
    # await worker_task：阻塞等待worker执行完毕，执行finally资源清理
    await self._worker_task
```

> 重点：`await self._worker_task` **不会主动终止 worker**，必须先 `put(None)` 发送退出信号。
> worker 收到 None 跳出循环，进入 `finally`，执行 `_shutdown_worker_resources()`。
> 
> 

```python
async def _shutdown_worker_resources(self) -> None:
    try:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
    except Exception as e:
        logger.warning("Error closing exit stack: %s", e)
    finally:
        self._session = None
        self._tools_meta = []
        self._initialized = False
        self._exit_stack = None
```

释放 AsyncExitStack 托管的全套 http、stream、session 资源，重置状态。

> 源码里还有一个 `_do_close`，当前实现和 close 重复，是待清理冗余方法。
> 
> 

## 10\. 队列核心知识点（你之前提问汇总）

1. `await queue.get()`：取出队首元素，**元素会从队列移除，只能消费一次**；

2. 队列为空：`await queue.get()` 协程阻塞挂起，**不会返回 None，不会退出循环**；

3. 哨兵机制：手动 `queue.put(None)`，`get()` 返回 None，break 退出 while 循环；

4. 单 worker 模型：队列只有一个协程消费，不存在多协程抢消息；

5. 任务丢失风险：get 拿到请求之后，如果 worker 在处理中途崩溃，这条请求直接丢失（队列里已经移除，无持久化）。

## 11\. 整套模型优势

1. **会话并发安全**：所有 MCP 读写都在单个 worker 协程执行，不需要锁；

2. **自动故障恢复**：调用异常自动销毁损坏会话，下一次请求自动重连；

3. **熔断保护**：MCP 服务持续失败，熔断器打开，快速失败，避免压垮 MCP 服务；

4. **资源统一管理**：AsyncExitStack 统一管理多层异步资源，关闭不会遗漏；

5. **优雅停机**：哨兵信号，worker 处理完当前正在执行任务后再退出，资源清理完整。

## 12\. 当前代码存在的风险点（工程重点）

1. **单 worker 串行瓶颈**：如果某个工具调用长时间卡住（超时设置不合理），整个队列阻塞，后续所有请求排队等待；

2. **队列无长度限制**：`asyncio.Queue()` 默认无限长，高并发场景下请求不断入队会内存暴涨；建议增加 `maxsize`；

3. worker 内部 `_handle_request` 的异常已经捕获，但是**worker 主循环如果出现未捕获异常，worker\_task 直接结束，队列残留任务全部无法处理**；

4. `_do_close` 方法冗余，和 close 逻辑重复，容易误用；

5. 重连只在 `_handle_request` 触发；如果 MCP 会话空闲时断开，要等到下一次工具调用才会感知并重连；

6. 没有任务超时：外部 call\_tool await future 没有超时，极端场景外部协程永久挂起。

## 13\. 完整调用时序

```Plain Text
业务层 → call_tool()
    → 熔断器校验
    → 封装_CallRequest，put进队列
    → await future 阻塞

worker _run循环：
    → await queue.get()拿到请求
    → _handle_request执行MCP调用（自动重连+重试）
    → request.future.set_result()

业务层：future被唤醒 → 返回结果

关闭流程：
    close() → put(None)
    worker get()拿到None → break while循环
    进入finally → _shutdown_worker_resources释放资源
    await self._worker_task 等待worker完全退出
```

---

你可以直接复制全部内容保存为 `MCP Session Worker 单工作线程队列模型.md`。

要不要我再单独提取一份**代码风险清单**，做成可直接提交给团队评审的简短版本？

> （注：部分内容由豆包工作 AI 生成）

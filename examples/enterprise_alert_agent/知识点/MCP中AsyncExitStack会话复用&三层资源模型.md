# MCP Client initialize() + AsyncExitStack 会话复用.md

## 完整源码片段
```python
async def initialize(self) -> bool:
    if self._initialized:
        return True
    headers: dict[str, str] = {}
    if settings.mcp_api_key:
        headers["X‑Access‑Key"] = settings.mcp_api_key
    if settings.mcp_cookie:
        headers["Cookie"] = settings.mcp_cookie

    stack = AsyncExitStack()
    try:
        # 用 AsyncExitStack 手动管理生命周期，session 在 initialize() 之后继续存活，供 call_tool 复用
        http_client = await stack.enter_async_context(
            create_mcp_http_client(
                headers=headers,
                timeout=httpx.Timeout(settings.mcp_connect_timeout_seconds),
            )
        )
        read, write, _ = await stack.enter_async_context(
            streamable_http_client(url=settings.mcp_service_url, http_client=http_client)
        )
        session = await stack.enter_async_context(
            ClientSession(read_stream=read, write_stream=write)
        )
        await asyncio.wait_for(
            session.initialize(), timeout=settings.mcp_connect_timeout_seconds
        )
        tools_results = await session.list_tools()
        self._tools_meta = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema or {},
                }
            }
            for tool in tools_results.tools
        ]
        self._session = session
        self._exit_stack = stack
        self._initialized = True
        logger.info("MCP client initialized successfully with %d tools", len(self._tools_meta))
        return True
    except (TimeoutError, Exception):
        logger.exception("MCP client initialization failed")
        await stack.aclose()  # 失败时释放已建立的连接，避免泄漏
        return False
```

## 核心目标
会话复用：通过 `AsyncExitStack` 将 `http_client` / `streamable_http_client` / `ClientSession` 的生命周期，从单次函数调用延长到 MCP Client 实例存活周期。
`initialize()` 建立一次长连接会话，后续多次 `call_tool()` 复用同一个 session，省去重复握手、建立流的网络开销。

## 三层资源职责说明
MCP 是双向流式 JSON‑RPC 协议，不是普通一次性 HTTP 请求响应，因此分层构建链路。

### 1. create_mcp_http_client 传输层
```python
http_client = await stack.enter_async_context(
    create_mcp_http_client(headers=headers, timeout=httpx.Timeout(...))
)
```
- 封装 httpx.AsyncClient，管理 TCP 连接池、HTTP 请求头、鉴权、IO 超时。
- 负责底层 TCP、TLS、HTTP 鉴权逻辑。
- 只懂 HTTP，**完全不感知 MCP 协议**。
- 价值：把鉴权、超时、代理统一收敛在此层，上层流与会话不需要关心底层HTTP细节。

### 2. streamable_http_client 流适配层
```python
read, write, _ = await stack.enter_async_context(
    streamable_http_client(url=settings.mcp_service_url, http_client=http_client)
)
```
- 基于上面的 http_client 建立 HTTP 长流，输出一对异步原始字节流：`read_stream`、`write_stream`。
- `read_stream`：读取MCP服务端推送过来的字节；`write_stream`：向MCP服务端发送字节。
- httpx 默认是请求‑响应模型，请求结束连接就断开；MCP 需要双向长连接，该组件屏蔽底层传输实现差异（SSE / HTTP‑stream）。
- 只处理 raw bytes，**不解析MCP JSON‑RPC报文**。

### 3. ClientSession MCP协议会话层
```python
session = await stack.enter_async_context(
    ClientSession(read_stream=read, write_stream=write)
)
```
- MCP SDK核心，接收一对原始字节流。
- 实现MCP JSON‑RPC完整逻辑：报文编解码、请求id配对、协议握手。
- 对外暴露业务方法：`session.initialize()`、`session.list_tools()`、`session.call_tool()`。
- 不关心底层传输是HTTP、stdio、websocket，只依赖 read / write 流抽象。

### 三层分层总览
|层级|组件|知晓内容|不感知内容|输出产物|
|---|---|---|---|---|
|传输层|create_mcp_http_client|TCP、HTTP头、鉴权、超时|MCP协议、JSON‑RPC|httpx异步http客户端|
|流适配层|streamable_http_client|HTTP长流、字节读写|MCP报文语义|read/write原始字节流|
|协议会话层|ClientSession|MCP JSON‑RPC、握手、工具调用|底层传输实现|MCP业务会话对象|

> 分层收益
1. 可替换：底层切换为stdio、websocket，只替换流适配层，上层ClientSession无需改动。
2. 职责单一：HTTP、字节流、协议逻辑解耦。
3. 统一生命周期：三层全部登记进同一个 AsyncExitStack，关闭逆序释放资源。

## AsyncExitStack 原理
普通 `async with` 嵌套，资源生命周期被限制在代码块内，出块自动aclose，无法复用session。
`AsyncExitStack.enter_async_context()`：
1. 将异步上下文管理器压入栈，进入上下文；
2. 将栈对象保存到实例变量 `self._exit_stack`，脱离函数局部作用域；
3. 后续调用 `await self._exit_stack.aclose()`，按后进先出顺序关闭所有登记资源：ClientSession → stream → http client。

> 初始化成功：`self._exit_stack = stack`，资源跟随实例存活，供后续 call_tool 复用。
> 初始化异常：局部执行 `await stack.aclose()`，释放已经打开的资源，防止连接泄漏，不保存栈到实例。

## 配套关闭方法（项目需要实现）
```python
async def close(self):
    if self._exit_stack is not None:
        await self._exit_stack.aclose()
        self._exit_stack = None
        self._session = None
        self._initialized = False
    logger.info("MCP client closed")
```
> 注意：异步资源不会被GC自动释放，应用关闭（FastAPI shutdown）必须显式调用 close，避免连接泄漏。

## 完整调用时序
```python
client = RemoteMCPClient()
ok = await client.initialize()   # 建立http+MCP双向长流，三层资源压栈保存到实例
if ok:
    res1 = await client.call_tool("tool_a", params1)
    res2 = await client.call_tool("tool_b", params2)
await client.close() # 统一释放全部三层资源
```

## 运行时数据流示例
`await session.call_tool("xxx", params)`
1. ClientSession 将调用封装为MCP JSON‑RPC报文，交给 write_stream。
2. streamable_http_client 将字节向下交给 http_client。
3. http_client 通过TCP发送至MCP服务端。

服务端返回结果：
1. http_client收到TCP字节。
2. streamable_http_client 通过 read_stream 吐出原始字节。
3. ClientSession 解析JSON‑RPC，匹配请求id，向上返回业务结果。

## 生产环境坑点
1. **忘记调用 close()**：AsyncExitStack不会GC自动释放异步资源，FastAPI shutdown钩子必须执行mcp client关闭，防止http连接泄漏。
2. **初始化成功后链路断开**：`_initialized=True`，但底层流已经失效；call_tool捕获异常，置位`_initialized=False`，上层执行重连。
3. **不要在初始化成功分支执行 stack.aclose()**，一旦关闭，session和流全部销毁，后续call_tool直接报错。
4. 两层超时区分：
    - `asyncio.wait_for`：MCP协议握手超时；
    - `httpx.Timeout`：底层HTTP IO超时。
5. 协程并发安全：多个协程同时调用 `initialize()` 会产生竞争；外部需要使用 `asyncio.Lock` 保护初始化流程，**禁止使用 threading.Lock**。

## 少任意一层的后果
1. 缺少 create_mcp_http_client：鉴权、超时、连接池逻辑散落在业务代码，难以统一管控。
2. 缺少 streamable_http_client：需要自己手写处理HTTP chunk、SSE字节流，耦合严重；切换传输方式要大改会话层。
3. 缺少 ClientSession：需要自己手写MCP JSON‑RPC编解码、请求id配对、握手逻辑，协议代码易错。
```

直接复制全部内容保存为 `MCP Client initialize() + AsyncExitStack 会话复用.md` 文件即可。
需要我把这份文档合并到之前 Agent&MCP 总文档里吗。

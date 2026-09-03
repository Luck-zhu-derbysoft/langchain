# Agent\&MCP工具注册与调用核心知识点\.md

## 一、核心架构思想：工具「描述」与「执行」完全分离

Agent 工具体系最核心的设计：**给大模型看的 Schema** 和 **代码真正执行的函数** 是两套独立数据，通过「工具名称」唯一绑定，职责彻底拆分。

解决问题：统一本地技能、远端MCP工具的调用口径，上层Agent无需区分工具来源，实现透明调用。

### 1\. available\_tools：LLM 视角（仅描述，无执行逻辑）

- **数据来源**：本地技能注册表 \+ MCP远端工具元数据

- **数据结构**：标准 OpenAI Function\-Call Schema 数组

- **包含内容**：工具名称、功能描述、参数类型、参数约束

- **核心作用**：喂给大模型，告诉模型「当前可用工具有哪些、该怎么传参」，指导模型生成工具调用指令

**关键特性**：纯静态描述，**无任何业务执行、网络调用逻辑**，不会真正运行工具。

**实操示例（标准Function\-Call Schema）**

```python
# available_tools 单条工具schema示例（本地/MCP工具通用结构）
{
    "type": "function",
    "function": {
        "name": "sql_query",
        "description": "执行数据库SQL查询，获取业务数据",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "合法的查询SQL语句"
                }
            },
            "required": ["sql"]
        }
    }
}
```

**底层代码对应**：

```python
# 本地工具元数据
available_tools: list[dict[str, Any]] = list(skill_registry.metadata())
# 合并MCP远端工具元数据
mcp_tool_metadata = get_tools_metadata()
available_tools.extend(mcp_tool_metadata)
```

### 2\. skill\_map / mcp\_tool\_map：代码执行视角（仅负责运行）

- **数据结构**：`dict[str, SkillFunc]`

- **key**：工具名称（必须和 Schema 中的 name 完全一致，是唯一关联纽带）

- **value**：**异步可执行函数**（本地原生函数 / MCP异步闭包函数）

- **核心作用**：接收LLM输出的工具名\+参数，代码从字典匹配函数，执行真实业务逻辑，打通AI决策与代码执行。

**实操示例（skill\_map 字典结构）**

```python
# 合并后的 skill_map 结构示例
skill_map = {
    # 本地技能：直接绑定本地同步/异步业务函数
    "get_local_time": local_time_tool,
    # MCP远端工具：绑定封装好的异步闭包函数
    "sql_query": <async function tool_closure>
}
```

**统一调用规范**（上层无感知区分本地/远端）：

```python
# LLM输出工具信息后，统一执行
func = skill_map[tool_name]
result = await func(tool_arguments)
```

## 二、MCP 异步闭包函数核心原理

MCP 远端工具无本地实现，通过**异步闭包封装网络调用**，伪装成本地异步函数，实现调用统一。

### 1\. 闭包核心特性

- 闭包函数在 `async_get_tool_map()` 内部定义

- 自动捕获上下文：MCP客户端实例、远端真实工具名

- 对外暴露极简签名：`async func(params: dict) -> Any`

- 内部封装完整 MCP 协议、网络请求逻辑，上层无需感知

### 2\. 闭包伪代码实现

```python
async def async_get_tool_map():
    # 获取MCP客户端
    client = async_get_mcp_client()
    # 拉取远端所有MCP工具列表
    remote_tools = await client.list_tools()

    mcp_tool_map = {}
    for tool in remote_tools:
        tool_name = tool["name"]
        
        # 异步闭包：捕获client、tool_name，对外仅接收业务参数
        async def tool_closure(params: dict, _name=tool_name, _client=client):
            # 内部封装MCP网络调用
            return await _client.call_tool(tool_name=_name, arguments=params)
        
        mcp_tool_map[tool_name] = tool_closure
    return mcp_tool_map
```

### 3\. 闭包设计优势

- 抹平本地工具与远端MCP工具的调用差异

- 异步实现，全程 `await`，不阻塞FastAPI事件循环线程

- 封装底层细节，降低上层Agent调用复杂度，实现业务无感知切换

**闭包调用落地示例**

```python
# 上层统一调用，无需区分本地/MCP
# 1. LLM 输出的调用参数
tool_name = "sql_query"
tool_args = {"sql": "select * from alert_log limit 10"}

# 2. 统一匹配执行，底层自动走MCP网络调用
exec_func = skill_map[tool_name]
result = await exec_func(tool_args)

# 3. 拿到远端MCP工具执行结果，回传给LLM
print("工具执行结果：", result)
```

## 三、本地技能 Side\-Effect 导入机制

### 1\. 核心代码

```python
# noqa: F401 屏蔽「导入未使用」告警
import app.infrastructure.skill
```

### 2\. 原理说明

- 此导入**不用于获取类/变量**，仅利用**模块加载副作用**

- 模块加载时，会自动执行所有技能的 `@register_skill` 装饰器

- 批量将所有本地技能注册到全局单例 `skill_registry`

### 3\. 关键注意点

缺失该导入 → 本地技能注册失效 → `skill_registry` 为空 → Agent 无法调用任何本地工具。

**落地示例（技能注册逻辑）**

```python
# app/infrastructure/skill/time_tool.py 本地技能示例
from app.infrastructure.skill.registry import skill_registry

@skill_registry.register("get_local_time", "获取当前系统时间")
async def get_local_time(params: dict):
    """本地纯内存工具，无网络IO"""
    from datetime import datetime
    return {"current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

# 当执行 import app.infrastructure.skill 时，本文件被加载，装饰器自动完成注册
```

## 四、工具合并规则与潜在风险

### 1\. 合并逻辑

```python
# MCP工具会覆盖本地同名工具
skill_map = {**skill_registry.skill_map(), **mcp_tool_map}
```

### 2\. 核心风险（生产高频坑）

- **工具名冲突**：MCP工具与本地工具重名时，MCP会强制覆盖本地工具，业务逻辑静默变更，难以排查

- **Schema与函数不匹配**：Schema名称、参数变更，但闭包函数未同步刷新，导致LLM传参正确、底层调用报错

- **重复网络IO**：每轮Agent请求都调用 `async_get_tool_map()`，频繁请求MCP服务，性能损耗

**风险复现示例（工具名覆盖）**

```python
# 1. 本地存在同名工具
@skill_registry.register("file_parse", "本地文件解析工具")
async def local_file_parse(params: dict):
    return {"local_parse_result": "本地解析完成"}

# 2. MCP服务也存在同名工具 file_parse
# 合并后 MCP工具覆盖本地工具，原有本地逻辑彻底失效，无任何报错提示
skill_map = {**skill_registry.skill_map(), **mcp_tool_map}

# 最终执行的是远端MCP文件解析，而非本地逻辑，业务静默异常
await skill_map["file_parse"](params)
```

## 五、延迟局部导入的作用

```python
# 函数内部局部导入，而非顶层导入
from app.infrastructure.mcp.mcp_client import async_get_tool_map
```

**解决核心问题**：规避 Agent 模块与 MCP 客户端模块的**循环导入依赖**，保证项目启动正常。

**循环导入问题示例 \& 优化对比**

```python
# ❌ 错误：顶层导入，触发循环依赖
# agent.py 顶层
from app.infrastructure.mcp.mcp_client import async_get_tool_map
# mcp_client.py 顶层又导入agent相关类型，启动直接报错

# ✅ 正确：函数局部延迟导入，运行时加载，规避启动循环依赖
async def _aresolve_tools(self, intent: dict[str, Any]) -> ToolsResolution:
    from app.infrastructure.mcp.mcp_client import async_get_tool_map
    # 后续正常执行逻辑
```

## 六、完整端到端工具调用链路

1. 项目启动：Side\-Effect 导入本地技能，完成批量注册

2. Agent请求触发：`_aresolve_tools()` 动态解析工具

3. 拉取本地Schema \+ 远端MCP Schema，合并为 `available_tools` 喂给LLM

4. 拉取本地技能函数 \+ MCP异步闭包，合并为 `skill_map` 执行字典

5. LLM思考，输出「工具名\+参数」的Function\-Call指令

6. Agent通过工具名匹配 `skill_map` 中的执行函数

7. `await`执行函数（本地直接运行 / MCP闭包发起远端网络调用）

8. 工具执行结果回传给LLM，完成一轮工具调用闭环

## 七、生产优化建议

1. 增加**工具名校重校验**，启动/解析时检测本地与MCP重名工具，主动告警

2. 对MCP工具列表做**缓存处理**，避免每轮请求重复拉取，减少IO开销

3. 增加MCP异常降级：MCP服务失败时，自动降级为仅使用本地工具

4. 保证 `async_get_tool_map()`与 `get_tools_metadata()` 成对刷新，避免Schema与执行逻辑不一致

**优化落地示例（MCP异常降级\+缓存简易实现）**

```python
# 优化后的 _aresolve_tools 核心逻辑
async def _aresolve_tools(self, intent: dict[str, Any]) -> ToolsResolution:
    from app.infrastructure.mcp.mcp_client import async_get_tool_map

    available_tools: list[dict[str, Any]] = list(skill_registry.metadata())
    mcp_tool_map: dict[str, SkillFunc] = {}
    mcp_tool_metadata: list = []

    # 增加缓存、异常降级
    if settings.mcp_enabled:
        try:
            # 可扩展：增加全局缓存，避免重复IO
            mcp_tool_map = await async_get_tool_map()
            mcp_tool_metadata = get_tools_metadata()
            available_tools.extend(mcp_tool_metadata)
        except Exception as e:
            logger.exception("MCP工具拉取失败，降级使用本地工具")
            mcp_tool_map = {}
            mcp_tool_metadata = []

    # 增加工具名校重校验
    local_keys = set(skill_registry.skill_map().keys())
    mcp_keys = set(mcp_tool_map.keys())
    conflict_keys = local_keys & mcp_keys
    if conflict_keys:
        logger.warning(f"检测到工具名冲突，MCP将覆盖本地工具：{conflict_keys}")

    skill_map = {**skill_registry.skill_map(), **mcp_tool_map}
    return ToolsResolution(available=available_tools, skill_map=skill_map, mcp_map=mcp_tool_map)


1 # SkillFunc 类型理解:
SkillFunc = Callable[..., dict[str, Any]]
# 合法：异步Skill（项目主流）
async def demo_skill(params: dict[str, Any]) -> dict[str, Any]:
    return {"code": 0, "data": "demo output"}

# MCP异步闭包同样符合SkillFunc
async def mcp_closure(params: dict) -> dict[str, Any]:
    return await mcp_client.call_tool("demo_tool", params)

# 封装到SkillDescriptor，frozen不可变
descriptor = SkillDescriptor(func=demo_skill)

# 执行：切记必须 await
resp = await descriptor.func({"arg1": 1})

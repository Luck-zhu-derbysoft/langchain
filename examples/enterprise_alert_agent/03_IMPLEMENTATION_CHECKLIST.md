"""
多任务并发执行系统 - 实现检查清单与关键要点
==============================================

本文档提供实现检查清单和关键点说明。
"""

## 📋 新增文件检查清单

### 文件创建步骤
- [ ] 创建目录: `app/infrastructure/agent/`
- [ ] 创建文件: `app/infrastructure/agent/__init__.py` (30行)
- [ ] 创建文件: `app/infrastructure/agent/agent_signature.py` (130行)
- [ ] 创建文件: `app/infrastructure/agent/a2a_protocol.py` (250行)
- [ ] 创建文件: `app/infrastructure/agent/agent_runtime.py` (450行)
- [ ] 创建文件: `app/infrastructure/agent/agent_coordinator.py` (400行)
- [ ] 创建文件: `app/infrastructure/agent/multi_task_executor.py` (350行)

**总计**: 6 个新文件，约 1600 行代码

---

## 📝 现有文件修改检查清单

### 1. app/schemas/chat.py
- [ ] 导入 Optional, Field 从 pydantic
- [ ] 修改 ChatResponse 类
- [ ] 新增 8 个字段:
  - [ ] intent: Optional[str]
  - [ ] intent_confidence: Optional[float]
  - [ ] selected_tool: Optional[str]
  - [ ] tool_confidence: Optional[float]
  - [ ] fallback_tools: list[str]
  - [ ] task_decomposed: bool
  - [ ] is_multi_task: bool
  - [ ] multi_task_results: Optional[dict]
  - [ ] failed_tasks: list[str]
  - [ ] manual_intervention_required: bool
  - [ ] trace_id: Optional[str]

### 2. app/application/services/chat_service.py

**导入部分**:
- [ ] from typing import Optional
- [ ] from dataclasses import dataclass, field
- [ ] import json
- [ ] from app.infrastructure.agent.agent_coordinator import (AgentCoordinator, MultiAgentOrchestrator)
- [ ] from app.infrastructure.agent.agent_signature import (AgentRegistry, PermissionScope)
- [ ] from app.infrastructure.agent.agent_runtime import (AgentRuntime, RetryPolicy, FallbackPolicy)
- [ ] from app.infrastructure.agent.a2a_protocol import (IntentClassification, ToolSelection, TaskDecomposition, TaskRequest, SubTask)
- [ ] from app.infrastructure.agent.multi_task_executor import (MultiTaskExecutor, ParallelTaskResult)

**修改 AgentState 类**:
- [ ] 添加 conversation_id: str
- [ ] 添加 intent_classification: Optional[IntentClassification]
- [ ] 添加 tool_selection: Optional[ToolSelection]
- [ ] 添加 task_decomposition: Optional[TaskDecomposition]
- [ ] 添加 is_multi_task: bool
- [ ] 添加 parallel_task_results: Optional[dict]
- [ ] 添加 task_execution_times: dict
- [ ] 添加 failed_task_ids: list[str]
- [ ] 添加 retry_count: int
- [ ] 添加 fallback_used: bool
- [ ] 添加其他 A2A 相关字段

**修改 ChatService.__init__()**:
- [ ] 添加参数: agent_registry: Optional[AgentRegistry] = None
- [ ] 添加参数: multi_agent_orchestrator: Optional[MultiAgentOrchestrator] = None
- [ ] 初始化: self.agent_runtime = None
- [ ] 初始化: self.multi_task_executor = None

**修改 ask() 方法**:
- [ ] 初始化 agent_state
- [ ] 初始化 agent_runtime 和 multi_task_executor
- [ ] 调用 _classify_intent() 并构建 IntentClassification
- [ ] 调用工具选择逻辑并构建 ToolSelection
- [ ] 添加任务拆解逻辑（检查 should_decompose）
- [ ] 添加多任务执行分支
- [ ] 调用 _merge_multi_task_results() 合并结果
- [ ] 修改 ChatResponse 构建，包含所有新字段

**新增方法**:
- [ ] 添加 _build_tool_executors() 方法 (~20 行)
- [ ] 添加 _decompose_task() 方法 (~60 行)
- [ ] 添加 _merge_multi_task_results() 方法 (~40 行)

### 3. app/main.py

**修改 create_app() 函数**:
- [ ] 添加导入: from app.infrastructure.agent.agent_signature import (...)
- [ ] 添加导入: from app.infrastructure.agent.agent_coordinator import MultiAgentOrchestrator
- [ ] 创建 agent_registry: AgentRegistry()
- [ ] 创建 multi_agent_orchestrator: MultiAgentOrchestrator()
- [ ] 创建 primary_agent_signature: AgentSignature(...)
- [ ] 注册 Agent 能力（至少5个）:
  - [ ] intent_classification
  - [ ] tool_selection
  - [ ] knowledge_retrieval
  - [ ] task_decomposition
  - [ ] multi_task_execution
- [ ] 配置权限: scopes=[READ, EXECUTE], max_concurrent_tasks=20
- [ ] 调用 register_agent()
- [ ] 设置 app.state.agent_registry
- [ ] 设置 app.state.multi_agent_orchestrator

### 4. app/api/routers/chat.py

**修改 get_chat_service() 函数**:
- [ ] 添加导入: from fastapi import Request
- [ ] 从 request.app.state 获取 agent_registry
- [ ] 从 request.app.state 获取 multi_agent_orchestrator
- [ ] 传递给 ChatService 构造函数

---

## 🔑 关键特性对应

### 1. 意图分类结果可见
```python
# 返回给客户端
response.intent                    # 分类意图 ⭐
response.intent_confidence         # 置信度（0-1）
response.trace_id                  # 追踪ID
```
**触发条件**: 所有请求
**实现位置**: ChatService.ask() 的意图分类节点

### 2. 工具选择理由可见
```python
# 返回给客户端
response.selected_tool             # 选择的工具
response.tool_confidence           # 选择置信度
response.fallback_tools            # 备选工具列表
```
**数据来源**: agent_state.tool_selection_reasoning
**实现位置**: ChatService.ask() 的工具选择节点

### 3. 任务拆解逻辑可见
```python
# 返回给客户端
response.task_decomposed           # 是否拆解
response.is_multi_task             # 是否多任务模式
response.multi_task_results {
    "completed_tasks": int,
    "failed_tasks": int,
    "total_tasks": int,
    "total_time": float,
    "parallel_efficiency": float,  # 并行效率 = 串行时间 / 并行时间
    "tools_used": list[str]
}
```
**触发条件**: 查询长度 > 200 或包含 "和"、"以及"、"同时"、"多个"
**实现位置**: MultiTaskExecutor.execute_decomposed_tasks()

### 4. 多任务并发执行
```python
# 执行流程
TaskDecomposition {
    subtasks: [SubTask],
    parallel_groups: [[task_ids]],  # 可并行执行的任务组
    dependencies: {task_id: [dep_ids]}
}

# 执行策略
1. 优先使用 parallel_groups
2. 按组使用 asyncio.gather() 并发
3. 如无并行组，按依赖关系顺序执行
```
**实现位置**: MultiTaskExecutor._execute_with_parallel_groups()

### 5. 重试和降级机制
```python
# 重试策略配置
RetryPolicy(
    strategy=EXPONENTIAL,  # 指数退避：delay = init * (factor ^ attempt)
    max_retries=3,
    initial_delay=1.0,
    max_delay=30.0,
    backoff_factor=2.0,
    jitter=True  # 避免雷群
)

# 降级策略配置
FallbackPolicy(
    strategy=USE_DEFAULT,  # 或 USE_CACHE, USE_ALTERNATIVE_TOOL, MANUAL_ESCALATION
    default_value=...,
    cache_key=...,
    enable_manual_escalation=True
)
```
**实现位置**: AgentRuntime._execute_with_retry()

### 6. 人工接管流程
```python
# 触发条件
- 所有重试失败
- fallback 也失败
- enable_manual_escalation = True

# 请求对象
ManualInterventionRequest {
    intervention_id: str,
    trigger_agent: str,
    reason: str,
    context: dict,
    suggested_action: str
}

# 返回给客户端
response.manual_intervention_required = True
response.trace_id  # 用于跟踪和人工处理
```
**实现位置**: AgentRuntime._request_manual_intervention()

---

## 💡 关键代码模式

### 模式1: 意图分类
```python
intent_classification = IntentClassification(
    intent=result.get("intent", "unknown"),
    confidence=result.get("confidence", 0.0),
    category=result.get("category", ""),
    entities=result.get("entities", {}),
    reasoning=result.get("reasoning", ""),
)
```

### 模式2: 工具选择
```python
tool_selection = ToolSelection(
    tool_name=available_tools[0].get("name", "unknown"),
    agent_id=primary_agent_id,
    confidence=_classify_intent_result.get("confidence", 0.0),
    fallback_tools=[t.get("name", "") for t in available_tools[1:3]],
    reasoning=f"Selected based on intent: {intent}",
)
```

### 模式3: 任务拆解
```python
should_decompose = (
    len(req.query) > 200 or 
    "和" in req.query or 
    "以及" in req.query
)

if should_decompose:
    decomposition = await self._decompose_task(query, intent, parent_run)
    if decomposition.subtasks and len(decomposition.subtasks) > 1:
        # 多任务模式
```

### 模式4: 多任务执行
```python
tool_executors = self._build_tool_executors(SKILL_MAP, mcp_tool_map)
result = await self.multi_task_executor.execute_decomposed_tasks(
    decomposition=decomposition,
    conversation_id=request_id,
    tool_executors=tool_executors,
    intent=intent_classification.intent,
    intent_confidence=intent_classification.confidence,
)

# 结果访问
result.completed_tasks       # 完成任务数
result.failed_tasks          # 失败任务数
result.total_execution_time  # 总耗时
result.parallel_efficiency   # 并行效率
result.task_results          # Dict[task_id -> TaskResponse]
```

---

## 🧪 验证清单

### 1. 导入验证
```bash
# 验证所有导入都存在
python -c "from app.infrastructure.agent import *"
```

### 2. 初始化验证
```bash
# 在 create_app() 后验证
app = create_app()
assert hasattr(app.state, 'agent_registry')
assert hasattr(app.state, 'multi_agent_orchestrator')
```

### 3. 功能验证

**意图分类**:
```python
# 发送请求
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "查询客户信息", "business_context": "CRM"}'

# 验证响应
{
  "intent": "query_customer",
  "intent_confidence": 0.92,
  "selected_tool": "db_query",
  ...
}
```

**多任务执行**:
```python
# 发送复杂查询
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "同时查询今天的告警数量和昨天的对比"}'

# 验证多任务响应
{
  "is_multi_task": true,
  "multi_task_results": {
    "completed_tasks": 2,
    "failed_tasks": 0,
    "parallel_efficiency": 1.8
  },
  ...
}
```

---

## ⚠️ 常见问题

### Q1: 如何判断是否触发多任务?
**A**: 查询条件:
- 长度 > 200 字符
- 包含 "和"、"以及"、"同时"、"多个"
- LLM 拆解结果包含 > 1 个 subtask

### Q2: 并行效率如何计算?
**A**: `parallel_efficiency = 串行执行总时间 / 并行执行总时间`
- 值 > 1 表示有加速效果
- 值 = 1 表示无加速
- 值 < 1 表示负向加速（不太可能，表示有较大开销）

### Q3: 降级策略如何选择?
**A**: 优先级顺序:
1. USE_DEFAULT (使用默认值)
2. USE_CACHE (使用缓存)
3. USE_ALTERNATIVE_TOOL (切换工具)
4. MANUAL_ESCALATION (人工接管)

### Q4: 如何追踪任务执行?
**A**: 使用 trace_id:
```python
# 所有响应都包含 trace_id = request_id
# 可用于关联日志和后续跟踪
logger.info(f"[{trace_id}] Task executed")
```

---

## 🚀 部署建议

### 1. 环境准备
```bash
# 确保依赖已安装
pip install pydantic asyncio

# 创建目录
mkdir -p app/infrastructure/agent
```

### 2. 分步实施
1. 先实现新的 6 个文件
2. 再修改 4 个现有文件
3. 从简单到复杂：
   - 第一步: 仅意图分类
   - 第二步: 添加工具选择
   - 第三步: 添加任务拆解
   - 第四步: 添加多任务执行

### 3. 测试建议
```bash
# 单元测试
pytest tests/unit_tests/agent/

# 集成测试
pytest tests/integration_tests/agent/

# 端到端测试
python scripts/test_multi_task.py
```

### 4. 监控指标
- 意图分类准确率
- 工具选择准确率
- 多任务并行效率
- 失败率和重试次数
- 人工接管次数

---

## 📚 文件对应关系

```
新增文件 (6个)
├── agent_signature.py      → 身份和权限
├── a2a_protocol.py         → 消息定义 ⭐
├── agent_runtime.py        → 重试降级
├── agent_coordinator.py    → 协调编排
├── multi_task_executor.py  → 多任务执行 ⭐
└── __init__.py             → 公共导出

修改文件 (4个)
├── chat_service.py         → 核心逻辑实现
├── schemas/chat.py         → 响应格式
├── main.py                 → 初始化
└── api/routers/chat.py     → 依赖注入
```

---

## 📊 性能预期

| 操作 | 耗时 | 并行加速 |
|------|------|---------|
| 单任务 | ~1-2s | - |
| 2 个并行任务 | ~1.5-2s | 1.5x |
| 3 个并行任务 | ~1.5-2s | 2.0x |
| 4 个并行任务 | ~2-3s | 2.5x |
| 5 个以上 | 受限于 max_concurrent_tasks | 递减 |

---

## ✅ 最终检查

实现完成后，验证以下项目:

- [ ] 所有 6 个新文件都已创建且语法正确
- [ ] 所有 4 个现有文件都已修改，无丢失内容
- [ ] 应用能够启动（python app/main.py）
- [ ] 单个查询能返回意图分类结果
- [ ] 工具选择返回理由信息
- [ ] 复杂查询能触发多任务执行
- [ ] 响应包含所有预期字段
- [ ] 日志显示多任务并发执行
- [ ] 错误处理和降级机制正常工作
- [ ] 人工接管流程可以触发


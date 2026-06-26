"""
多任务并发执行系统 - 现有文件修改清单
========================================

详细列出对现有4个文件的所有修改点和代码。
"""

# ============================================================================
# 修改文件 1: app/schemas/chat.py
# ============================================================================

"""
修改点: 扩展 ChatResponse 类，添加多任务相关字段

替换或添加以下代码：
"""

# 在 imports 中添加
from typing import Optional
from pydantic import BaseModel, Field

# 替换或修改 ChatResponse 类
class ChatResponse(BaseModel):
    """聊天响应模型"""
    answer: str = Field(..., description="回答内容")
    citations: list = Field(default_factory=list, description="引用来源")
    model: str = Field(..., description="使用的模型名称")
    request_id: str = Field(..., description="请求唯一ID")
    
    # ======= 新增 A2A 字段 =======
    intent: Optional[str] = Field(
        default=None, 
        description="分类的用户意图"
    )
    intent_confidence: Optional[float] = Field(
        default=None, 
        ge=0.0, 
        le=1.0,
        description="意图分类置信度"
    )
    selected_tool: Optional[str] = Field(
        default=None, 
        description="选择使用的工具名称"
    )
    tool_confidence: Optional[float] = Field(
        default=None, 
        ge=0.0, 
        le=1.0,
        description="工具选择置信度"
    )
    fallback_tools: list[str] = Field(
        default_factory=list, 
        description="备选工具列表"
    )
    task_decomposed: bool = Field(
        default=False, 
        description="是否进行了任务拆解"
    )
    
    # ======= 新增多任务字段 =======
    is_multi_task: bool = Field(
        default=False, 
        description="是否多任务模式"
    )
    multi_task_results: Optional[dict] = Field(
        default=None,
        description="多任务执行结果统计 {completed_tasks, failed_tasks, total_tasks, total_time, parallel_efficiency, tools_used}"
    )
    failed_tasks: list[str] = Field(
        default_factory=list,
        description="失败的任务ID列表"
    )
    
    manual_intervention_required: bool = Field(
        default=False, 
        description="是否需要人工接管"
    )
    trace_id: Optional[str] = Field(
        default=None, 
        description="A2A 链路追踪ID"
    )


# ============================================================================
# 修改文件 2: app/application/services/chat_service.py
# ============================================================================

"""
修改点1: 在文件顶部添加新的导入
"""

# 添加以下导入
from typing import Optional
from dataclasses import dataclass, field
import json
from app.infrastructure.agent.agent_coordinator import (
    AgentCoordinator, 
    MultiAgentOrchestrator
)
from app.infrastructure.agent.agent_signature import AgentRegistry, PermissionScope
from app.infrastructure.agent.agent_runtime import (
    AgentRuntime, 
    RetryPolicy, 
    FallbackPolicy
)
from app.infrastructure.agent.a2a_protocol import (
    IntentClassification, 
    ToolSelection, 
    TaskDecomposition,
    TaskRequest,
    SubTask,
)
from app.infrastructure.agent.multi_task_executor import MultiTaskExecutor, ParallelTaskResult


"""
修改点2: 增强 AgentState 数据类 - 添加所有新字段
"""

@dataclass
class AgentState:
    # 原有字段保留
    answer: str = ""
    current_turn_count: int = 0
    token_counter: list[int] = field(default_factory=lambda: [0])
    previous_tool_calls: set[str] = field(default_factory=set)
    read_only_mode: bool = False
    tool_error_count: int = 0
    
    # ======= 新增 A2A 相关字段 =======
    conversation_id: str = ""
    intent_classification: Optional[IntentClassification] = None
    tool_selection: Optional[ToolSelection] = None
    task_decomposition: Optional[TaskDecomposition] = None
    
    # ======= 新增多任务字段 =======
    is_multi_task: bool = False
    parallel_task_results: Optional[dict] = None
    task_execution_times: dict = field(default_factory=dict)
    failed_task_ids: list[str] = field(default_factory=list)
    
    # 重试和降级
    retry_count: int = 0
    fallback_used: bool = False
    
    # 人工接管
    manual_intervention_requested: bool = False
    manual_intervention_id: Optional[str] = None
    
    # 指标收集
    intent_confidence_scores: list[float] = field(default_factory=list)
    tool_selection_reasoning: str = ""


"""
修改点3: 增强 ChatService 类的 __init__ 方法
"""

class ChatService:
    def __init__(
        self,
        model_client: ModelClient,
        retriever: Retriever,
        trace: LangSmithTracer,
        memory: RedisPostgresConversationMemoryStore,
        # 新增参数
        agent_registry: Optional[AgentRegistry] = None,
        multi_agent_orchestrator: Optional[MultiAgentOrchestrator] = None,
    ) -> None:
        self.model_client = model_client
        self.retriever = retriever
        self.trace = trace
        self.memory = memory
        
        # Agent 基础设施
        self.agent_registry = agent_registry
        self.multi_agent_orchestrator = multi_agent_orchestrator
        self.agent_runtime = None
        self.multi_task_executor = None  # 新增多任务执行器


"""
修改点4: ask() 方法开始处的初始化逻辑

在 ask() 方法中，找到 request_id 创建的地方，添加以下初始化：
"""

def ask(self, req: ChatRequest, *, parent_run: RunTree | None = None) -> ChatResponse:
    ask_run = self.trace.start_child(...)
    request_id = str(uuid.uuid4())
    
    # ======= 新增初始化 =======
    # 初始化状态
    agent_state = AgentState(conversation_id=request_id)
    
    # 初始化运行时（如果有注册表）
    if not self.agent_runtime and self.agent_registry:
        self.agent_runtime = AgentRuntime(
            agent_id="primary_agent",
            default_retry_policy=RetryPolicy(max_retries=2),
            default_fallback_policy=FallbackPolicy(),
        )
    
    # 初始化多任务执行器
    if not self.multi_task_executor and self.agent_runtime:
        self.multi_task_executor = MultiTaskExecutor(self.agent_runtime)


"""
修改点5: 意图分类逻辑

在 ask() 方法中，替换或增强现有的意图分类部分：
"""

# 节点1：意图分类
_classify_intent_result = self._classify_intent(
    req.query, self.model_client, parent_run=ask_run
)

# 构建 IntentClassification 对象
intent_classification = IntentClassification(
    intent=_classify_intent_result.get("intent", "unknown"),
    confidence=_classify_intent_result.get("confidence", 0.0),
    category=_classify_intent_result.get("category", ""),
    entities=_classify_intent_result.get("entities", {}),
    reasoning=_classify_intent_result.get("reasoning", ""),
)

agent_state.intent_classification = intent_classification
agent_state.intent_confidence_scores.append(intent_classification.confidence)

logger.info(
    "[%s] Intent classified: intent=%s confidence=%.2f reasoning=%s",
    request_id, 
    intent_classification.intent, 
    intent_classification.confidence,
    intent_classification.reasoning[:100] if intent_classification.reasoning else ""
)


"""
修改点6: 工具选择逻辑

在工具解析后添加结构化工具选择决策：
"""

# 节点2：工具解析和选择
available_tools, SKILL_MAP, mcp_tool_map = self._resolve_tools(_classify_intent_result)

if available_tools:
    primary_agent_id = "primary_agent"
    tool_selection = ToolSelection(
        tool_name=available_tools[0].get("name", "unknown"),
        agent_id=primary_agent_id,
        confidence=_classify_intent_result.get("confidence", 0.0),
        fallback_tools=[t.get("name", "") for t in available_tools[1:3]],
        reasoning=f"Selected based on intent: {intent_classification.intent} and user context",
    )
    
    agent_state.tool_selection = tool_selection
    agent_state.tool_selection_reasoning = tool_selection.reasoning
    
    logger.info(
        "[%s] Tool selected: tool=%s confidence=%.2f reasoning=%s",
        request_id, 
        tool_selection.tool_name, 
        tool_selection.confidence,
        tool_selection.reasoning[:100] if tool_selection.reasoning else ""
    )


"""
修改点7: 任务拆解和多任务执行逻辑 - 关键！

在工具选择后、RAG 检索前添加（或替换现有的单任务逻辑）：
"""

# ======= 节点3：任务拆解和多任务执行 =======
# 检查是否需要拆解任务
should_decompose = (
    len(req.query) > 200 or 
    "和" in req.query or 
    "以及" in req.query or
    "同时" in req.query or
    "多个" in req.query
)

if should_decompose:
    try:
        # 调用任务拆解器
        task_decomposition = await self._decompose_task(
            query=req.query,
            intent=intent_classification.intent,
            parent_run=ask_run,
        )
        
        agent_state.task_decomposition = task_decomposition
        agent_state.is_multi_task = len(task_decomposition.subtasks) > 1
        
        logger.info(
            "[%s] Task decomposed: subtasks=%d parallel_groups=%d is_multi_task=%s",
            request_id, 
            len(task_decomposition.subtasks),
            len(task_decomposition.parallel_groups),
            agent_state.is_multi_task
        )
        
        # ======= 执行多任务流程 =======
        if agent_state.is_multi_task and self.multi_task_executor:
            try:
                # 构建工具执行器映射
                tool_executors = self._build_tool_executors(SKILL_MAP, mcp_tool_map)
                
                # 执行并行任务
                parallel_result: ParallelTaskResult = await self.multi_task_executor.execute_decomposed_tasks(
                    decomposition=task_decomposition,
                    conversation_id=request_id,
                    tool_executors=tool_executors,
                    intent=intent_classification.intent,
                    intent_confidence=intent_classification.confidence,
                )
                
                # 保存结果
                agent_state.parallel_task_results = {
                    "completed_tasks": parallel_result.completed_tasks,
                    "failed_tasks": parallel_result.failed_tasks,
                    "total_tasks": parallel_result.total_tasks,
                    "total_time": parallel_result.total_execution_time,
                    "parallel_efficiency": parallel_result.parallel_efficiency,
                    "tools_used": parallel_result.tools_used,
                }
                
                # 收集失败任务
                agent_state.failed_task_ids = [
                    task_id for task_id, response in parallel_result.task_results.items()
                    if not response.success
                ]
                
                # 合并回答
                agent_state.answer = await self._merge_multi_task_results(
                    parallel_result=parallel_result,
                    business_context=req.business_context
                )
                
                logger.info(
                    "[%s] Multi-task execution completed: completed=%d failed=%d time=%.2fs",
                    request_id, 
                    parallel_result.completed_tasks,
                    parallel_result.failed_tasks,
                    parallel_result.total_execution_time
                )
                
            except Exception as e:
                logger.exception("[%s] Multi-task execution failed, falling back to single task", request_id)
                agent_state.is_multi_task = False
        
        # 如果不是多任务或多任务失败，降级为单任务
        if not agent_state.is_multi_task or not agent_state.parallel_task_results:
            logger.info("[%s] Falling back to single task mode", request_id)
            # 原有的单任务逻辑继续执行
    
    except Exception as e:
        logger.warning("[%s] Task decomposition failed: %s", request_id, e)
        agent_state.is_multi_task = False
else:
    agent_state.is_multi_task = False


"""
修改点8: 新增辅助方法 1 - 构建工具执行器

在 ChatService 类中添加新方法：
"""

def _build_tool_executors(
    self,
    SKILL_MAP: dict,
    mcp_tool_map: dict,
) -> dict:
    """构建工具执行器映射 - 用于多任务执行"""
    tool_executors = {}
    
    # 添加技能工具
    for tool_name, tool_func in SKILL_MAP.items():
        tool_executors[tool_name] = lambda params, f=tool_func: f(**params)
    
    # 添加 MCP 工具
    for tool_name, tool_func in mcp_tool_map.items():
        tool_executors[tool_name] = lambda params, f=tool_func: f(**params)
    
    logger.info("Built %d tool executors", len(tool_executors))
    return tool_executors


"""
修改点9: 新增辅助方法 2 - 任务拆解

在 ChatService 类中添加新方法：
"""

async def _decompose_task(
    self,
    query: str,
    intent: str,
    parent_run: RunTree | None = None,
) -> TaskDecomposition:
    """拆解任务为子任务"""
    
    system_prompt = f"""
    你是任务拆解专家。请将用户的查询拆解为具体的可执行子任务。
    用户意图: {intent}
    
    请返回 JSON 格式，包含：
    {{
        "subtasks": [
            {{"name": "子任务名", "type": "类型", "tool": "所需工具", "parameters": {{...}}}}
        ],
        "dependencies": {{"task_id": ["依赖的task_id"]}},
        "parallel_groups": [[可并行执行的task_id]]
    }}
    
    重要：
    - 每个子任务应该相互独立或有清晰的依赖关系
    - 可以并行执行的任务应该放在同一个 parallel_groups 中
    - 参数应该包含执行该子任务所需的所有信息
    """
    
    try:
        result = self.model_client.chat(
            user_query=query,
            system_prompt=system_prompt,
            parent_run=parent_run,
        )
        
        # 解析结果
        parsed = json.loads(result)
        
        # 构建子任务
        subtasks = []
        for idx, st_data in enumerate(parsed.get("subtasks", [])):
            subtask = SubTask(
                name=st_data.get("name", f"Task {idx}"),
                description=st_data.get("description", ""),
                task_type=st_data.get("type", "general"),
                required_tool=st_data.get("tool"),
                parameters=st_data.get("parameters", {}),
                priority=st_data.get("priority", 0),
            )
            subtasks.append(subtask)
        
        # 构建依赖关系
        dependencies = {}
        for task_id, deps in parsed.get("dependencies", {}).items():
            dependencies[task_id] = deps
        
        # 获取执行顺序
        execution_order = parsed.get("execution_order", [])
        
        # 获取并行组
        parallel_groups = parsed.get("parallel_groups", [])
        
        decomposition = TaskDecomposition(
            main_task=query,
            subtasks=subtasks,
            dependencies=dependencies,
            execution_order=execution_order,
            parallel_groups=parallel_groups,
        )
        
        return decomposition
        
    except Exception as e:
        logger.warning("Task decomposition failed: %s, returning default", e)
        return TaskDecomposition(main_task=query)


"""
修改点10: 新增辅助方法 3 - 合并多任务结果

在 ChatService 类中添加新方法：
"""

async def _merge_multi_task_results(
    self,
    parallel_result: ParallelTaskResult,
    business_context: str = "",
) -> str:
    """合并多个任务的结果为单一回答"""
    
    # 收集成功任务的结果
    successful_results = []
    for task_id, task_response in parallel_result.task_results.items():
        if task_response.success and task_response.result:
            successful_results.append({
                "task_id": task_id,
                "result": task_response.result,
                "execution_time": task_response.execution_time,
            })
    
    if not successful_results:
        return "无法获取任务执行结果。请稍后重试。"
    
    # 使用 LLM 合并结果
    merge_prompt = f"""
    请综合以下多个任务的执行结果，生成一个完整、连贯的回答。
    
    任务结果 (共 {len(successful_results)} 个):
    {json.dumps(successful_results, ensure_ascii=False, indent=2)}
    
    业务上下文: {business_context}
    
    要求：
    - 整合所有任务的结果
    - 避免重复信息
    - 按照逻辑顺序组织内容
    - 保持内容准确和完整
    """
    
    try:
        merged_answer = self.model_client.chat(
            user_query="请合并以上结果生成回答",
            system_prompt=merge_prompt,
        )
        return merged_answer
    except Exception as e:
        logger.exception("Failed to merge results")
        return "无法合并任务结果。"


"""
修改点11: ChatResponse 构建

在 ask() 方法末尾，替换或增强现有的 ChatResponse 构建逻辑：
"""

# 构建响应
response = ChatResponse(
    answer=agent_state.answer,
    citations=citations,
    model=settings.model_name,
    request_id=request_id,
    
    # A2A 字段
    intent=agent_state.intent_classification.intent if agent_state.intent_classification else None,
    intent_confidence=agent_state.intent_classification.confidence if agent_state.intent_classification else None,
    selected_tool=agent_state.tool_selection.tool_name if agent_state.tool_selection else None,
    tool_confidence=agent_state.tool_selection.confidence if agent_state.tool_selection else None,
    fallback_tools=agent_state.tool_selection.fallback_tools if agent_state.tool_selection else [],
    task_decomposed=agent_state.task_decomposition is not None,
    
    # 多任务字段
    is_multi_task=agent_state.is_multi_task,
    multi_task_results=agent_state.parallel_task_results,
    failed_tasks=agent_state.failed_task_ids,
    
    manual_intervention_required=agent_state.manual_intervention_requested,
    trace_id=request_id,
)

return response


# ============================================================================
# 修改文件 3: app/main.py
# ============================================================================

"""
修改点: 在 create_app() 函数中初始化 Agent 基础设施

找到 create_app() 函数，在创建 FastAPI 应用之前添加以下代码：
"""

def create_app() -> FastAPI:
    configure_langsmith()
    
    from app.api.routers.chat import router as chat_router
    from app.api.routers.health import router as health_router
    from app.api.routers.ingest import router as ingest_router
    
    # ======= 新增：初始化 Agent 基础设施 =======
    from app.infrastructure.agent.agent_signature import (
        AgentRegistry, 
        AgentSignature, 
        AgentCapability,
        AgentCredential, 
        AgentPermission, 
        PermissionScope
    )
    from app.infrastructure.agent.agent_coordinator import MultiAgentOrchestrator
    from app.config.settings import settings
    
    agent_registry = AgentRegistry()
    multi_agent_orchestrator = MultiAgentOrchestrator()
    
    # 注册主 Agent
    primary_agent_signature = AgentSignature(
        credential=AgentCredential(
            agent_id="primary_agent",
            agent_name="主 RAG Agent",
            issuer="system",
        ),
        capabilities=[
            AgentCapability(
                name="intent_classification",
                description="用户意图分类和识别",
                tags=["nlp", "classification"],
            ),
            AgentCapability(
                name="tool_selection",
                description="选择合适的工具执行用户请求",
                tags=["tool", "execution"],
            ),
            AgentCapability(
                name="knowledge_retrieval",
                description="从向量库检索相关知识",
                tags=["rag", "retrieval"],
            ),
            AgentCapability(
                name="task_decomposition",
                description="将复杂任务拆解为子任务",
                tags=["planning", "decomposition"],
            ),
            AgentCapability(
                name="multi_task_execution",
                description="并行执行多个任务，提高效率",
                tags=["execution", "parallel"],
            ),
        ],
        permissions=AgentPermission(
            agent_id="primary_agent",
            scopes=[PermissionScope.READ, PermissionScope.EXECUTE],
            resource_tags=["knowledge_base", "tools", "database"],
            allowed_tools=["db_query", "knowledge_search", "time_skill", "alert_management"],
            max_concurrent_tasks=20,
        ),
        endpoint=f"http://localhost:{settings.port or 8000}",
        metadata={
            "version": "0.3.0",
            "max_concurrent_tasks": 20,
            "supports_multi_task": True,
            "supports_parallel_execution": True,
        },
    )
    
    multi_agent_orchestrator.register_agent(primary_agent_signature)
    agent_registry.register(primary_agent_signature)
    
    # 创建 FastAPI 应用
    app = FastAPI(
        title=settings.app_name,
        version="0.3.0",
    )
    
    # 存储到 app.state
    app.state.agent_registry = agent_registry
    app.state.multi_agent_orchestrator = multi_agent_orchestrator
    
    # 包含路由
    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(ingest_router)
    app.state.limiter = limiter
    
    # ... 其他配置保持不变 ...
    
    return app


# ============================================================================
# 修改文件 4: app/api/routers/chat.py
# ============================================================================

"""
修改点: 在 get_chat_service 依赖函数中注入 Agent 组件

找到 get_chat_service() 或类似的依赖注入函数，修改如下：
"""

from fastapi import Request

# 假设这是现有的依赖注入函数
async def get_chat_service(request: Request) -> ChatService:
    """获取 ChatService 依赖"""
    
    # ... 现有代码（获取 model_client, retriever, tracer, memory） ...
    model_client = ...
    retriever = ...
    tracer = ...
    memory = ...
    
    # ======= 新增：从 app.state 获取 Agent 基础设施 =======
    agent_registry = getattr(request.app.state, 'agent_registry', None)
    multi_agent_orchestrator = getattr(request.app.state, 'multi_agent_orchestrator', None)
    
    return ChatService(
        model_client=model_client,
        retriever=retriever,
        trace=tracer,
        memory=memory,
        # 新增参数
        agent_registry=agent_registry,
        multi_agent_orchestrator=multi_agent_orchestrator,
    )


# ============================================================================
# 总结
# ============================================================================

"""
修改汇总：

1. app/schemas/chat.py
   - 修改: ChatResponse 类
   - 新增: 8 个字段 (intent, intent_confidence, selected_tool, tool_confidence, 
           fallback_tools, task_decomposed, is_multi_task, multi_task_results, 
           failed_tasks, manual_intervention_required, trace_id)

2. app/application/services/chat_service.py
   - 修改: 导入 (10+ 新导入)
   - 修改: AgentState 数据类 (11 个新字段)
   - 修改: __init__() 方法 (2 行新代码)
   - 修改: ask() 方法 (3 个新节点、30+ 行代码)
   - 新增: _build_tool_executors() 方法
   - 新增: _decompose_task() 方法
   - 新增: _merge_multi_task_results() 方法

3. app/main.py
   - 修改: create_app() 函数
   - 新增: Agent 初始化代码 (60+ 行)

4. app/api/routers/chat.py
   - 修改: get_chat_service() 函数 (3 行新代码)

关键改动：
- 添加了对 A2A 协议的支持（IntentClassification、ToolSelection、TaskDecomposition）
- 添加了多任务执行能力（MultiTaskExecutor）
- 支持任务拆解和并行执行
- 支持结果合并和指标上报
"""

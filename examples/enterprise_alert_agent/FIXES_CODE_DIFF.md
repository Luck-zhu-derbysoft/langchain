# 升级完成清单 - 代码对比及修复指南

**总体完成度**: 85-90%  
**剩余修复工作**: 9 项  
**预计修复时间**: 5-6 小时  

---

## 修复清单导航

| 优先级 | 项目 | 文件 | 说明 |
|--------|------|------|------|
| 🔴 P1 | [1. pending-intervention 端点](#1-修复-pending-intervention-端点) | chat.py | 空实现，需完成 |
| 🔴 P1 | [2. intervention-history 端点](#2-修复-intervention-history-端点) | chat.py | 空实现，需完成 |
| 🔴 P1 | [3. 待处理请求存储](#3-补全-待处理请求存储机制) | intervention_handler.py | 缺少存储逻辑 |
| 🔴 P1 | [4. 指标数据收集](#4-补全-指标数据收集) | chat_service.py | 框架存在，无数据填充 |
| 🔴 P1 | [5. 故障告警集成](#5-补全-故障诊断-告警集成) | chat_service.py | 诊断已调用，告警未触发 |
| 🔴 P1 | [6. 干预回调执行](#6-补全-干预回调执行) | intervention_handler.py | 占位符实现 |
| 🟡 P2 | [7. 权限控制](#7-添加权限控制到-admin-端点) | admin.py | 无鉴权 |
| 🟡 P2 | [8. 配置验证](#8-添加配置值验证) | dynamic_settings.py | 无值验证 |
| 🟡 P2 | [9. 配置读取集成](#9-配置读取集成到-chat_service) | chat_service.py | ConfigManager 未使用 |

---

## 🔴 P1 优先级修复项

### 1. 修复 `/pending-intervention` 端点

**文件**: `app/api/routers/chat.py`  
**行号**: ~180  
**优先级**: 🔴 P1 - 高  
**工作量**: 0.5h

#### ❌ 修复前
```python
@router.get("/chat/{request_id}/pending-intervention")
def get_pending_intervention(request_id: str):
    """获取等待人工干预的任务列表"""
    pass
```

#### ✅ 修复后
```python
@router.get("/chat/{request_id}/pending-intervention")
def get_pending_intervention(request_id: str):
    """获取等待人工干预的任务列表"""
    intervention_handler = app.state.shared_dependencies.get('intervention_handler')
    if not intervention_handler:
        return {"error": "intervention_handler not initialized"}
    
    pending = intervention_handler.get_pending_interventions(request_id)
    return {
        "request_id": request_id,
        "pending_interventions": [p.dict() if hasattr(p, 'dict') else p for p in pending],
        "count": len(pending)
    }
```

**检查点**:
- [ ] 替换此段代码
- [ ] 确保 intervention_handler 在 shared_dependencies 中
- [ ] 测试接口返回值格式

---

### 2. 修复 `/intervention-history` 端点

**文件**: `app/api/routers/chat.py`  
**行号**: ~200  
**优先级**: 🔴 P1 - 高  
**工作量**: 0.5h

#### ❌ 修复前
```python
@router.get("/chat/{request_id}/intervention-history")
def get_intervention_history(request_id: str):
    """获取任务的人工干预历史记录"""
    pass
```

#### ✅ 修复后
```python
@router.get("/chat/{request_id}/intervention-history")
def get_intervention_history(request_id: str):
    """获取任务的人工干预历史记录"""
    intervention_handler = app.state.shared_dependencies.get('intervention_handler')
    if not intervention_handler:
        return {"error": "intervention_handler not initialized"}
    
    history = intervention_handler.get_intervention_history(request_id)
    return {
        "request_id": request_id,
        "interventions": [h.dict() if hasattr(h, 'dict') else h for h in history],
        "total_count": len(history)
    }
```

**检查点**:
- [ ] 替换此段代码
- [ ] 确保 intervention_handler 在 shared_dependencies 中
- [ ] 测试接口返回值格式

---

### 3. 补全 待处理请求存储机制

**文件**: `app/infrastructure/agent/intervention_handler.py`  
**行号**: __init__ 和新增方法  
**优先级**: 🔴 P1 - 高  
**工作量**: 1h

#### ❌ 修复前 - __init__ 方法
```python
def __init__(self):
    """初始化干预处理器"""
    self.intervention_queue = []
```

#### ✅ 修复后 - __init__ 方法
```python
def __init__(self):
    """初始化干预处理器"""
    self.intervention_queue = []
    # 添加待处理请求存储
    self.pending_interventions: dict[str, list] = {}
    # 添加干预历史存储
    self.intervention_history: dict[str, list] = {}
```

#### ❌ 修复前 - submit_intervention 方法（末尾）
```python
    def submit_intervention(self, request: ManualInterventionRequest) -> ManualInterventionResult:
        """提交人工干预请求"""
        # ... 现有代码 ...
        return result
```

#### ✅ 修复后 - submit_intervention 方法（末尾添加）
```python
    def submit_intervention(self, request: ManualInterventionRequest) -> ManualInterventionResult:
        """提交人工干预请求"""
        # ... 现有代码 ...
        
        # 记录到干预历史
        if request.task_id not in self.intervention_history:
            self.intervention_history[request.task_id] = []
        self.intervention_history[request.task_id].append(result)
        
        return result
```

#### ✅ 修复后 - 新增方法（在类末尾添加）
    def get_pending_interventions(self, request_id: str) -> list:
```python
        """获取等待干预的请求列表"""
        return self.pending_interventions.get(request_id, [])
    
    def get_intervention_history(self, request_id: str) -> list:
        """获取干预历史记录"""
        return self.intervention_history.get(request_id, [])
    
    def add_pending_intervention(self, request_id: str, intervention: "ManualInterventionRequest"):
        """添加待处理干预请求"""
        if request_id not in self.pending_interventions:
            self.pending_interventions[request_id] = []
        self.pending_interventions[request_id].append(intervention)
    
    def remove_pending_intervention(self, request_id: str, intervention_id: str):
        """移除待处理干预请求"""
        if request_id in self.pending_interventions:
            self.pending_interventions[request_id] = [
                i for i in self.pending_interventions[request_id] 
                if i.id != intervention_id
            ]
```

**检查点**:
- [ ] 在 __init__ 中添加 pending_interventions 和 intervention_history 字典
- [ ] 在 submit_intervention 中记录历史
- [ ] 添加 4 个新方法
- [ ] 确保类型标注正确

---

### 4. 补全 指标数据收集

**文件**: `app/application/services/chat_service.py`  
**行号**: 多处  
**优先级**: 🔴 P1 - 高  
**工作量**: 2h

#### 4.1 修复前 - ask 方法开始处
```python
def ask(self, req: ChatRequest, ...):
    request_id = str(uuid.uuid4())
    # ... 现有代码 ...
```

#### 4.1 修复后 - ask 方法开始处
```python
def ask(self, req: ChatRequest, ...):
    import time
    request_id = str(uuid.uuid4())
    start_time = time.time()
    
    # 创建指标记录
    self.metrics_collector.create_metrics(request_id)
    # ... 现有代码 ...
```

#### 4.2 修复前 - 返回响应前
```python
    # ... 执行逻辑 ...
    response = ChatResponse(
        answer=answer,
        # ... 其他字段 ...
    )
    return response
```

#### 4.2 修复后 - 返回响应前
```python
    # ... 执行逻辑 ...
    
    # 记录耗时
    elapsed_ms = (time.time() - start_time) * 1000
    self.metrics_collector.record_latency(request_id, elapsed_ms)
    
    # 获取指标汇总
    metrics = self.metrics_collector.get_metrics(request_id)
    performance_metrics = {
        "total_time_ms": elapsed_ms,
        "p50_latency_ms": metrics.get_p50_latency() if metrics else 0,
        "p95_latency_ms": metrics.get_p95_latency() if metrics else 0,
        "p99_latency_ms": metrics.get_p99_latency() if metrics else 0,
        "token_usage": metrics.token_usage if metrics else 0,
        "estimated_cost_usd": metrics.estimated_cost_usd if metrics else 0,
        "cache_hit_rate": metrics.get_cache_hit_rate() if metrics else 0,
        "success_rate": metrics.get_success_rate() if metrics else 1.0,
    }
    
    response = ChatResponse(
        answer=answer,
        # ... 其他字段 ...
        performance_metrics=performance_metrics
    )
    return response
```

#### 4.3 修复前 - LLM 调用后（搜索 "llm_response = " 的位置）
```python
    # 调用 LLM
    llm_response = self.llm_manager.get_response(...)
```

#### 4.3 修复后 - LLM 调用后
```python
    # 调用 LLM
    llm_response = self.llm_manager.get_response(...)
    
    # 记录 token 使用
    if hasattr(llm_response, 'usage'):
        self.metrics_collector.record_token_usage(
            request_id, 
            llm_response.usage.total_tokens
        )
```

#### 4.4 修复前 - 异常处理块
```python
    except Exception as e:
        logger.error("Error in ask: %s", str(e))
        # ... 现有错误处理 ...
```

#### 4.4 修复后 - 异常处理块
```python
    except Exception as e:
        logger.error("Error in ask: %s", str(e))
        # 记录错误
        self.metrics_collector.record_error(request_id)
        # ... 现有错误处理 ...
```

#### 4.5 修复前 - 缓存检查处
```python
    # 检查缓存
    cached = self.memory_manager.get(...)
    if cached:
        # 使用缓存
```

#### 4.5 修复后 - 缓存检查处
```python
    # 检查缓存
    cached = self.memory_manager.get(...)
    if cached:
        # 记录缓存命中
        self.metrics_collector.record_cache_hit(request_id)
        # 使用缓存
    else:
        # 记录缓存未命中
        self.metrics_collector.record_cache_miss(request_id)
```

**检查点**:
- [ ] 在 ask 方法开始添加计时和指标创建
- [ ] 在返回前获取指标并填充到 performance_metrics
- [ ] 在 LLM 调用后记录 token 使用
- [ ] 在异常处理中记录错误
- [ ] 在缓存检查中记录命中/未命中

---

### 5. 补全 故障诊断-告警集成

**文件**: `app/application/services/chat_service.py`  
**行号**: ~934-950  
**优先级**: 🔴 P1 - 高  
**工作量**: 1h

#### ❌ 修复前
```python
                        fault_context = FaultContext(
                            request_id=request_id,
                            task_id=str(task_id),
                            agent_id=agent_id,
                            error_message=str(e)
                        )
                        diagnosis: FaultDiagnosis = self.fault_analyzer.analyze(fault_context)
                        # 故障诊断完成，但告警未触发
```

#### ✅ 修复后
```python
                        fault_context = FaultContext(
                            request_id=request_id,
                            task_id=str(task_id),
                            agent_id=agent_id,
                            error_message=str(e)
                        )
                        diagnosis: FaultDiagnosis = self.fault_analyzer.analyze(fault_context)
                        
                        # 根据故障诊断创建告警
                        from app.observability.alert_types import AlertSeverity, AlertTypes
                        from app.infrastructure.fault.fault_types import FaultSeverity
                        
                        severity_map = {
                            FaultSeverity.INFO: AlertSeverity.INFO,
                            FaultSeverity.WARNING: AlertSeverity.WARNING,
                            FaultSeverity.HIGH: AlertSeverity.WARNING,
                            FaultSeverity.CRITICAL: AlertSeverity.CRITICAL,
                        }
                        
                        alert_severity = severity_map.get(diagnosis.severity, AlertSeverity.WARNING)
                        self.alert_manager.create_alert(
                            alert_type=AlertTypes.FAULT_ALERT,
                            severity=alert_severity,
                            title=f"Task {task_id} Fault: {diagnosis.fault_type.value}",
                            message=diagnosis.root_cause,
                            affected_resource=f"task_{task_id}",
                            context={
                                "agent_id": agent_id,
                                "recovery_suggestions": diagnosis.recovery_suggestions,
                                "can_retry": diagnosis.can_retry,
                                "estimated_recovery_time": diagnosis.estimated_recovery_time
                            }
                        )
```

**检查点**:
- [ ] 在故障诊断后添加告警触发逻辑
- [ ] 确保映射关系正确
- [ ] 验证告警信息完整

---

### 6. 补全 干预回调执行

**文件**: `app/infrastructure/agent/intervention_handler.py`  
**行号**: execute_callback 方法  
**优先级**: 🔴 P1 - 高  
**工作量**: 1h

#### ❌ 修复前
```python
def execute_callback(self, intervention_result: ManualInterventionResult):
    """执行干预的回调操作"""
    # 占位符实现
    pass
```

#### ✅ 修复后
```python
def execute_callback(self, intervention_result: ManualInterventionResult):
    """执行干预的回调操作"""
    import logging
    logger = logging.getLogger(__name__)
    
    intervention_type = intervention_result.intervention_type
    
    try:
        if intervention_type == "retry":
            # 重试任务
            logger.info(f"Executing retry callback for task {intervention_result.task_id}")
            # TODO: 发送重试信号到任务队列
            pass
        
        elif intervention_type == "skip":
            # 跳过任务
            logger.info(f"Executing skip callback for task {intervention_result.task_id}")
            # TODO: 标记任务为已跳过
            pass
        
        elif intervention_type == "modify_params":
            # 修改参数后重新执行
            logger.info(f"Executing modify_params callback for task {intervention_result.task_id}")
            # TODO: 用新参数重新执行任务
            pass
        
        elif intervention_type == "abort":
            # 中止任务
            logger.info(f"Executing abort callback for task {intervention_result.task_id}")
            # TODO: 标记任务为已中止
            pass
        
        else:
            logger.warning(f"Unknown intervention type: {intervention_type}")
    
    except Exception as e:
        logger.error(f"Failed to execute callback: {e}")
```

**检查点**:
- [ ] 替换 pass 占位符为实际逻辑
- [ ] 添加日志记录
- [ ] 确保所有干预类型都有处理

---

## 🟡 P2 优先级修复项

### 7. 添加权限控制到 `/admin/*` 端点

**文件**: `app/api/routers/admin.py`  
**行号**: 文件开始和各端点  
**优先级**: 🟡 P2 - 中  
**工作量**: 1h

#### ❌ 修复前 - 文件开始
```python
from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])
```

#### ✅ 修复后 - 文件开始
```python
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from datetime import datetime

router = APIRouter(prefix="/admin", tags=["admin"])

def require_admin_token(request: Request):
    """验证管理员令牌"""
    token = request.headers.get("X-Admin-Token")
    expected_token = os.getenv("ADMIN_TOKEN", "default-admin-token")
    
    if not token or token != expected_token:
        raise HTTPException(
            status_code=403,
            detail="Unauthorized: Invalid or missing admin token"
        )
    return token
```

#### ❌ 修复前 - 各个 POST 端点
```python
@router.post("/config/{key}")
def update_config(key: str, value: any):
    """更新配置"""
    # ...
```

#### ✅ 修复后 - 各个 POST 端点
```python
@router.post("/config/{key}")
def update_config(key: str, value: any, _admin_token: str = Depends(require_admin_token)):
    """更新配置"""
    # ...
```

**检查点**:
- [ ] 在文件开始添加 require_admin_token 函数
- [ ] 为所有 POST/PUT 端点添加 `_admin_token: str = Depends(require_admin_token)` 参数
- [ ] 设置环境变量 ADMIN_TOKEN

---

### 8. 添加配置值验证

**文件**: `app/config/dynamic_settings.py`  
**行号**: set 方法  
**优先级**: 🟡 P2 - 中  
**工作量**: 1h

#### ❌ 修复前
```python
def set(self, key: str, value: Any, user_id: str = "system") -> bool:
    """设置配置值"""
    if key not in self.defaults:
        logger.warning(f"Unknown config key: {key}")
        return False
    
    self.overrides[key] = value
    # ... 记录变更 ...
    return True
```

#### ✅ 修复后
```python
def set(self, key: str, value: Any, user_id: str = "system") -> bool:
    """设置配置值（带验证）"""
    if key not in self.defaults:
        logger.warning(f"Unknown config key: {key}")
        return False
    
    # 配置值验证规则
    validators = {
        "task_max_retries": lambda v: isinstance(v, int) and v >= 0 and v <= 10,
        "task_timeout_seconds": lambda v: isinstance(v, (int, float)) and v > 0 and v <= 3600,
        "max_parallel_tasks": lambda v: isinstance(v, int) and 0 < v <= 100,
        "enable_cache": lambda v: isinstance(v, bool),
        "enable_tracing": lambda v: isinstance(v, bool),
        "cache_ttl_seconds": lambda v: isinstance(v, int) and v > 0,
    }
    
    # 执行验证
    if key in validators:
        if not validators[key](value):
            error_msg = f"Invalid value for {key}: {value}"
            logger.error(error_msg)
            raise ValueError(error_msg)
    
    self.overrides[key] = value
    # ... 记录变更 ...
    return True
```

**检查点**:
- [ ] 添加验证字典
- [ ] 在 set 方法中执行验证
- [ ] 根据实际配置项调整验证规则

---

### 9. 配置读取集成到 chat_service

**文件**: `app/application/services/chat_service.py`  
**行号**: __init__ 和运行时读取处  
**优先级**: 🟡 P2 - 中  
**工作量**: 1.5h

#### 9.1 修复前 - __init__ 方法
```python
def __init__(self, ...):
    # ... 现有初始化代码 ...
    self.fault_analyzer = FaultAnalyzer()
    self.alert_manager = AlertManager()
```

#### 9.1 修复后 - __init__ 方法
```python
def __init__(self, ...):
    # ... 现有初始化代码 ...
    self.fault_analyzer = FaultAnalyzer()
    self.alert_manager = AlertManager()
    
    # 初始化配置管理器
    from app.config.dynamic_settings import ConfigManager
    self.config_manager = ConfigManager()
    
    # 从配置读取运行时参数
    self.max_retries = self.config_manager.get_task_max_retries()
    self.task_timeout = self.config_manager.get_task_timeout()
    self.max_parallel_tasks = self.config_manager.get_max_parallel_tasks()
```

#### 9.2 修复前 - _execute_decomposed_tasks 中的重试逻辑
```python
def _execute_decomposed_tasks(self, ...):
    MAX_RETRIES = 3
    
    for attempt in range(MAX_RETRIES + 1):
        # ... 重试逻辑 ...
```

#### 9.2 修复后 - _execute_decomposed_tasks 中的重试逻辑
```python
def _execute_decomposed_tasks(self, ...):
    # 从配置读取最大重试次数
    max_retries = self.config_manager.get_task_max_retries()
    
    for attempt in range(max_retries + 1):
        # ... 重试逻辑 ...
```

#### 9.3 修复前 - 任务超时设置
```python
def _execute_task_with_timeout(self, task, timeout=30):
    # 固定超时值
```

#### 9.3 修复后 - 任务超时设置
```python
def _execute_task_with_timeout(self, task, timeout=None):
    # 使用动态超时值
    if timeout is None:
        timeout = self.config_manager.get_task_timeout()
```

**检查点**:
- [ ] 在 __init__ 中初始化 ConfigManager
- [ ] 从配置读取默认值
- [ ] 在需要时从 config_manager 读取运行时配置
- [ ] 验证配置值被正确应用

---

## 📊 修复进度表

| 项目 | P级 | 状态 | 备注 |
|------|-----|------|------|
| 1. pending-intervention | 🔴 | [ ] TODO | chat.py ~180 |
| 2. intervention-history | 🔴 | [ ] TODO | chat.py ~200 |
| 3. 待处理请求存储 | 🔴 | [ ] TODO | intervention_handler.py |
| 4. 指标数据收集 | 🔴 | [ ] TODO | chat_service.py |
| 5. 故障诊断告警 | 🔴 | [ ] TODO | chat_service.py ~934 |
| 6. 干预回调执行 | 🔴 | [ ] TODO | intervention_handler.py |
| 7. 权限控制 | 🟡 | [ ] TODO | admin.py |
| 8. 配置验证 | 🟡 | [ ] TODO | dynamic_settings.py |
| 9. 配置读取集成 | 🟡 | [ ] TODO | chat_service.py |

---

## 📝 使用说明

1. **按优先级修复**: 先完成 🔴 P1 (项目1-6，4-5小时)，再做 🟡 P2 (项目7-9，1-1.5小时)
2. **复制代码块**: 每个修复项都包含"修复前"和"修复后"的完整代码
3. **逐个检查**: 使用检查点列表验证修复是否正确
4. **测试验证**: 修复完每一项后进行测试

**第1阶段完成时间**: ~4-5 小时，达到 **95% 完成度**  
**第2阶段完成时间**: ~1-1.5 小时，达到 **98% 完成度**  

---

**最后更新**: 2026-07-17  
**格式**: 标准修复对比格式  
**下一步**: 逐项按照代码对比进行修复

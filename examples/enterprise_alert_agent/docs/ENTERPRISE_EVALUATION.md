# enterprise_alert_agent 企业级评估与升级路线图

> 文档版本：v1.0（2026-08-08）
> 评估方式：全量代码审查（核心链路逐行阅读 + 全基础设施扫描 + 关键风险点二次验证）
> 用途：作为系统从「原型」升级到「企业级生产可用」的整改依据

---

## 目录

## 一、总体结论

**定位：这是一个架构清晰、方向正确的高质量「原型/Demo」，但离「企业级生产可用」还有明显差距。**

- **做得好的地方**：分层架构规范（`api → application → infrastructure → observability`）、LangSmith 全链路追踪纪律、多 Agent 编排与注册表+熔断、故障诊断与告警、双级记忆（Redis+PG）、任务分解并行执行、PII 脱敏、JWT/RBAC 脚手架——这些组件确实存在且大多可运行。
- **致命短板**：认证体系可被零成本绕过、生产密钥硬编码在代码里、存在真实的多线程/异步 Bug、审计与指标只存内存、测试近乎为零。
  
## 二、成熟度评分表

| 维度 | 评分 | 一句话 |
|---|---|---|
| 架构分层与可读性 | 8.5 | 分层清晰，依赖注入规范，命名良好 |
| 可观测性（追踪/指标/告警） | 7.5 | 追踪很扎实；指标/告警有真实 Bug 且仅内存 |
| 多 Agent 编排与容错 | 6.5 | 概念完整（注册表/熔断/降级/DLQ），但多处未真正接线 |
| 安全性 | **2.0** | 认证可绕过、密钥入库，当前不可对外暴露 |
| 数据持久化与一致性 | 5.0 | PG 连接池是死代码；Redis 错误被静默吞掉 |
| 可测试性 | **2.5** | 仅 1 个调用真实付费 API 的集成测试，零单元测试 |
| 多租户隔离 | 3.0 | `tenant_id` 有默认值且无任何强制隔离 |
| 资源管理与性能边界 | 4.0 | 指标无界增长、ingest 无大小限制、同步阻塞式 IO |
| 审计与合规 | 3.5 | 审计记录大部分只在内存，文件日志逻辑是坏的 |
| **综合** | **≈4.5** | 优秀原型，未达生产级 |

**评分口径**：10 分为「可直接承载生产流量」；5 分以下代表存在阻断性缺陷。

---

## 三、架构概览

```mermaid
flowchart TB
    subgraph API层
        CHAT["/chat/*<br/>stream / memory / intervention / alerts / metrics"]
        ING["/ingest/*<br/>text / file / stats"]
        ADM["/admin/*<br/>token / config"]
        HEA["/health"]
    end
    subgraph 应用层
        CS["ChatService<br/>意图分类→工具选择→RAG→ReAct循环→任务分解→并行执行"]
        IS["IngestService"]
    end
    subgraph 基础设施层
        ORC["MultiAgentOrchestrator + AgentRegistry(熔断)"]
        MEM["MemoryStore<br/>Redis(快) + PostgreSQL(源)"]
        VEC["ChromaStore + Retriever(融合重排)"]
        LLM["ModelClient<br/>主模型+降级+重试"]
        MCP["MCP Client"]
        DLQ["DeadLetterQueue"]
        INT["InterventionHandler(人机协同)"]
        AUD["AuditLogger"]
    end
    subgraph 可观测层
        TRC["LangSmithTracer"]
        MET["MetricsCollector"]
        ALM["AlertManager"]


**技术栈**：Python 3.11+ / FastAPI / Pydantic v2 / OpenAI SDK（DashScope 兼容 `qwen-plus` / `qwen-turbo`）/ ChromaDB / Redis / PostgreSQL（psycopg3）/ LangSmith / slowapi / PyJWT / MCP SDK / 线程池多 Agent 编排。


## 四、P0 级问题（上线前必须修复）

### 4.1 认证体系可被完全绕过 (已修复)

| 项 | 说明 |
|---|---|
| 位置 | `app/api/routers/admin.py:22`、`app/infrastructure/security/auth.py` |
| 问题 | `POST /admin/token` **无需任何凭证**，请求体直接带 `{user_id, role}`，任何人都能给自己签发 `role: "admin"` 的 JWT |
| 影响 | RBAC（`require_roles`）只挂在 `/admin/*`；`/chat/*`、`/ingest/*`、`/health` 全部**无认证**。当前 JWT/RBAC 是「摆设」 |
| 修复方向 | ① token 端点需接入真实凭证（密码/API Key/OAuth）；② 聊天/摄入端点统一接入认证中间件；③ 权限按租户维度校验 |

### 4.2 生产密钥硬编码在代码中 (已修复)

| 项 | 说明 |
|---|---|
| 位置 | `app/config/settings.py:64,78,87,112` |
| 问题 | 代码内直接写入 `mysql_password = "MyPass_2026_secure"`、`redis_password = "MyRedisPass123!"`、`pg_password = "postgres"`、`admin_jwt_secret = "CHANGE_ME_IN_ENV"`，以及明文公网 IP `139.224.246.172` |
| 影响 | `admin_jwt_secret` 用默认值 + HS256 = **任何人可伪造管理员 JWT**；密码入库一旦提交即泄露 |
| 修复方向 | ① 所有密钥默认值为空，强制从环境变量/Secrets Manager 读取，缺失即启动失败；② 项目根添加 `.gitignore`（`.env`、`data/chroma_db/`、`*.sqlite3`）；③ 添加 `.env.example` 模板；④ 轮换已泄露的密码与密钥 |

> 附：`admin_jwt_exp_minutes` 在 settings 中已定义但从未使用，`create_access_token` 内写死 15 分钟。


### 4.3 真实的多线程异步 Bug (已修复)

| 项 | 说明 |
|---|---|
| 位置 | `app/observability/alert_manager.py:48`、`app/application/services/chat_service.py`（`_execute_decomposed_tasks`） |
| 问题 | `AlertManager.create_alert()` 调用 `asyncio.create_task(...)`，但它在 `ThreadPoolExecutor` **工作线程**中被调用 → 必然抛 `RuntimeError: no running event loop` |
| 影响 | 多任务故障路径下的告警**永远不会真正分发**（异常被外层故障处理吞掉）；邮件/短信/SMS 分发也全部是 `TODO` 占位 |
| 修复方向 | ① 用「线程安全队列 + 独立分发线程」或 `run_coroutine_threadsafe` 替代 `asyncio.create_task`；② 接入真实通知渠道或先落库 |

---
根据文档内容，修复五、P1 级问题3 | **限流** 。给出对比修改的代码，我手动修复
## 五、P1 级问题（生产化关键缺口）

| # | 领域 | 问题 | 位置 |
|---|---|---|---|
| 1 | **审计合规** | 审计记录基本只存内存（上限 1 万条），`audit.jsonl` 仅在内存溢出瞬间写一条；`CHAT_REQUEST/INTERVENTION/LOGIN` 动作定义了但从未上报；配置变更日志记录 `new_value`（可能含密钥） | `app/infrastructure/audit/audit_logger.py` |
| 2 | **测试** | 仅 `tests/integration/test_chat_api.py` 一个文件，全部调用**真实付费 DashScope API**，且写污染真实 Chroma/Redis/PG，无清理；零单元测试；无 CI | `tests/` |
| 3 (已修复)| **限流** | 仅 `/chat/stream` 有 10 次/分钟/IP 限流；`/ingest`（上传文件）、`/admin`、干预接口全部无限制；按 IP 而非按用户/租户 | `app/api/routers/chat.py:35` |
| 4 | **多租户**(已修复) | `tenant_id/user_id/thread_id` 均有默认值 `"default-*"`；检索、记忆、缓存均无强制租户隔离参数 → 多租户边界纯属「建议性」 | `app/schemas/chat.py` |
| 5 | **连接管理** | PG `ConnectionPool` 是死代码：`_pg_conn` 每次操作新建连接后关闭；Redis 所有操作 `except Exception: pass` 静默失败 | `app/infrastructure/memory/redis_postgres_conversation_memory.py` |
| 6 | **DLQ 只写不读** | `dead_letter_queue.add()` 有调用，但 `retry()/list_pending()/stats()` 没有任何路由或后台 Worker 触发 → DLQ 实际是「只写」的黑洞 | `app/infrastructure/queue/dlq_handler.py` |
| 7 | **人机协同未接线** | 人工干预只有手动 API 可触达；`chat_service` 中 `manual_intervention_required` 恒为 `False`；`create_intervention_request()` 从未被调用；`execute_intervention()` 未真正接线回调；`finally` 块无条件把结果写成 `success=False` 覆盖真实结果 | `app/infrastructure/agent/intervention_handler.py` |
| 8 | **动态配置部分失效** | `ChatService.__init__` 从 `ConfigManager` 读取，但 `_run_agent_loop`/`_execute_decomposed_tasks` 内部直接读 `settings.*` → 管理端改了这些 key 不生效；`DynamicSettings` 无锁、无持久化、value 无校验；`/admin/config/{key}` 无 key 白名单 | `app/application/services/chat_service.py`、`app/config/dynamic_settings.py`、`app/api/routers/admin.py` |
| 9 | **资源边界** | `MetricsCollector` 无界增长（内存泄漏）、无锁（多线程写竞争）；`get_summary` 忽略 `request_id` 聚合所有数据；`/ingest` 对 `content` 与上传文件**无大小限制**（DoS 面）；`BudgetExceededError` 未被捕获 → 落入 500 | `app/observability/metrics.py`、`app/api/routers/ingest.py`、`app/infrastructure/llm/model_client.py` |
| 10 | **线程安全** | `AgentRegistry`、`Retriever` 缓存、`MetricsCollector`、`InterventionHandler`、`DynamicSettings` 在并行任务线程中被并发读写却无锁 | 多处 |

---

## 六、P2 级问题（打磨完善项）

- **Rerank 是「假」的**：`flashrank` 依赖与 `rerank_model="ms-marco-MiniLM-L-12-v2"` 已声明但从未使用；所谓 hybrid 只是「稠密余弦 + 词法重叠」加权融合，非真正的 BM25 混合检索/交叉编码器重排。位置：`app/rag/retrieval/retriever.py`。
- **A2A 是空壳**：`A2AProtocol` 只是 set/get 存根，无真实线协议；`from sqlalchemy import Enum` 遮蔽内置 `Enum`。位置：`app/infrastructure/agent/a2a_protocol.py`。
- **死代码/未用依赖**：`passlib[bcrypt]`（无密码哈希使用）、`alert_rules` 从未填充、`admin_jwt_exp_minutes` 未用、`data/` 下 `mock_checkpoint.py`/`mock_memoryStore.py`/`langsmith_demo.py` 不属于应用。
- **同步阻塞**：`time.sleep(backoff)` 在重试路径阻塞线程；psycopg/redis 均为同步驱动。
- **MCP 客户端**：每次调用新建会话（浪费）；`call_tool` 无超时（可能挂起）；`logger.exception("...%s")` 缺 `e` 参数（格式化 Bug）；工具无客户端 allowlist，模型可见远端全部工具。位置：`app/infrastructure/mcp/mcp_client.py`、`mcp_service.py`。
- **告警模型**：`Alert.acknowledged_at` 默认 `datetime.utcnow()`（语义上应为 `None`）；`alert_manager.py` 存在重复导入 `from sqlalchemy import true`。
- **流式降级**：`stream_chat()` 无重试/降级逻辑（单次尝试）；`ModelClient` 重试无指数退避（立即重试）。

---

## 七、系统优点（应保留的资产）✅

1. **架构纪律优秀**：`api → application → infrastructure → observability → rag → schemas` 分层干净，依赖通过 `app.state.shared_dependencies` 注入，命名与注释质量高。
2. **追踪很扎实**：`LangSmithTracer` 显式管理 `RunTree` 生命周期，服务/检索/LLM/工具各层都有父子 run，错误统一 `format_error` 收口。
3. **多 Agent 概念完整**：注册表 + 能力匹配 + 优先级 + 熔断（连续失败 3 次断开、30s 自动恢复）+ 子任务按 Agent 分配。
4. **任务编排有真实逻辑**：意图分类 → 工具选择 → 依赖感知的批处理并行执行（含依赖环检测）、失败子任务隔离、备选工具链降级。
5. **记忆双级设计合理**：Redis 快路径 + PG 事实源，`SELECT ... FOR UPDATE` 保证 turn 计数串行化，PII 正则脱敏（手机/身份证/邮箱）。
6. **初始化 SQL 规范**：`scripts/init_pg_memory_schema.sql` 幂等、有部分索引，与代码 SQL 一致。
7. **容错思维到位**：模型双模型降级 + 重试、工具失败阈值降级为纯 RAG、故障诊断分严重级映射告警。

---

## 八、分模块审计明细

### 8.1 API 层（`app/api/routers/`）

| 模块 | 端点 | 发现 |
|---|---|---|
| `chat.py` | `POST /chat/stream`、`/memory/clear`、`/{id}/pending-intervention`、`/{id}/intervention`、`/{id}/intervention-history`、`/alerts`、`/alerts/{id}/acknowledge`、`/metrics/{id}` | 唯一限流端点 `/chat/stream`（10/分钟/IP）；全部无认证 |
| `ingest.py` | `POST /ingest/text`、`/ingest/file`、`GET /ingest/stats` | 无认证/无限流/无大小限制；模块级与 `main.py` 各建一份 `ChromaStore`（同一 collection 两个实例） |
| `admin.py` | `POST /admin/token`、`config/*` 系列 | **无凭证签发任意角色 token**；config 写入无 key 白名单 |
| `health.py` | `GET /health` | 无 DB/Redis 依赖检查（只报模型就绪状态） |
| 全局 | — | 无 CORS、无 TrustedHost、无请求 ID 中间件、无认证中间件 |

### 8.2 安全（`app/infrastructure/security/auth.py`，60 行）

- ✅ 真实实现：`Role` 枚举、`TokenPayload`、`create_access_token`、`get_current_user`、`require_roles`。
- ❌ 仅挂在 `/admin/*`；`/admin/token` 无凭证校验 → 整套 RBAC 可被绕过。
- ❌ `datetime.utcnow()` 已弃用（Python 3.12+）。
- ❌ `passlib[bcrypt]` 声明但从未使用（无密码哈希）。

### 8.3 记忆（`app/infrastructure/memory/redis_postgres_conversation_memory.py`，325 行）

- ✅ Redis 快路径 + PG 事实源；`FOR UPDATE` 串行化 turn 计数；TTL 7 天；PII 脱敏。
- ❌ **连接池死代码**：`__init__` 建的 `ConnectionPool` 被类级 `_pg_conn` 覆盖，每次操作新建连接；嵌套 `__del__` 为死代码。
- ❌ Redis 全部 `except Exception: pass` → 连接问题不可见。
- ❌ 写穿缓存仅当 key 已存在时更新 → TTL 过期后缓存陈旧。

### 8.4 Agent 基础设施（`app/infrastructure/agent/`）

| 文件 | 发现 |
|---|---|
| `agent_registry.py` | 熔断逻辑正确；**无锁**，被并行任务线程并发读写 |
| `agent_coordinator.py` | 薄封装：按工具/能力选 Agent、测量耗时、上报健康；无超时控制 |
| `a2a_protocol.py` | 纯数据类；`MessageType` 非真实枚举；`A2AProtocol` 是存根；死代码 |
| `intervention_handler.py` | 无锁；`execute_intervention()` 未接线真实回调；`finally` 覆盖真实结果；`create_intervention_request()` 从未被调用 |

### 8.5 MCP（`app/infrastructure/mcp/`）

- ✅ 模块级单例 + `asyncio.Lock` 双重检查；启动时 `async_init_mcp()`。
- ❌ `mcp_service.call_tool()` 每次调用新建连接/会话；无超时。
- ❌ 模型可见远端全部工具，无客户端 allowlist/参数校验。
- ❌ 认证仅 header（`X-Access-Key`/`Cookie`），默认空 = 未认证调用。
- ❌ `logger.exception("MCP client call failed: %s")` 缺 `e` 参数（格式化 Bug）。

### 8.6 队列 / DLQ（`app/infrastructure/queue/dlq_handler.py`，174 行）

- ✅ 内存 + Redis 持久化（`dlq:entry:{id}` / `dlq:index`），`threading.Lock` 保护。
- ❌ **只写不读**：`retry()/list_pending()/stats()` 无 API/后台 worker 触发，`time.sleep` 阻塞式重试。
- ❌ Redis key 无 TTL/上限 → 无界增长。

### 8.7 可观测（`app/observability/`）

| 文件 | 发现 |
|---|---|
| `metrics.py` | 内存 dict 无锁/无淘汰（泄漏）；`get_summary` 忽略 `request_id` 聚合全部；成本为硬编码估算 `tokens/1000*0.002` |
| `alert_manager.py` | **`asyncio.create_task` 从线程调用 → RuntimeError**；`alert_rules` 从未填充；通知渠道全为 `TODO`；重复导入 |
| `alert_types.py` | `acknowledged_at` 默认 `utcnow()`（应为 `None`） |
| `langsmith_tracer.py` | ✅ 质量好：显式 RunTree 生命周期、`_enabled` 门控、`format_error` |

### 8.8 审计（`app/infrastructure/audit/audit_logger.py`，90 行）

- ✅ 动作类型枚举（chat/config/intervention/login/permission）、`threading.Lock`。
- ❌ 记录基本只在内存（上限 1 万条）；`audit.jsonl` 只在溢出瞬间写一条 → **不是真正的追加式审计日志**，重启即丢。
- ❌ 仅 `CONFIG_CHANGE` 被实际调用；detail 未脱敏（可能含密钥）；`ip_address`/`request_id` 从未填充。

### 8.9 配置（`app/config/`）

| 文件 | 发现 |
|---|---|
| `settings.py` | pydantic-settings + `.env`；**硬编码密钥/公网 IP**；`admin_jwt_exp_minutes` 未用 |
| `dynamic_settings.py` | 内存 `_overrides` 无持久化/无锁/无值校验；`ChatService` 部分读取但部分路径直接读 `settings.*` 导致动态配置部分失效 |
| `tracing_config.py` | ✅ 环境变量优先级正确（env > settings），`.env` 加载路径正确 |

### 8.10 测试（`tests/`）

- ❌ 仅 `tests/integration/test_chat_api.py`（4 个用例）：`health`、`chat_validation`、`retrieval_hit_after_ingest`、`time_query`。
- ❌ 全部调用真实付费 DashScope API；写污染真实 Chroma/Redis/PG；无 teardown。
- ❌ 无认证、干预、DLQ、记忆、编排、限流（429）、错误路径、多任务覆盖；**零单元测试**；无 CI。

### 8.11 模式层（`app/schemas/`）

- `ChatRequest`：`tenant_id/user_id/thread_id` 有默认值 → 多租户隔离无强制。
- `IngestTextRequest`：`content` 无最大长度限制。

### 8.12 LLM（`app/infrastructure/llm/model_client.py`，216 行）

- ✅ 主模型 + 降级模型、重试分类（仅网络类可重试）、token 预算、LangSmith 追踪、启动探针。
- ❌ 无指数退避（立即重试）；`stream_chat` 无重试/降级；`BudgetExceededError` 未捕获 → 500；无 LLM 层熔断。

### 8.13 向量 / RAG（`app/infrastructure/vectorstore/`、`app/rag/`）

| 文件 | 发现 |
|---|---|
| `chroma_store.py` | PersistentClient + cosine；`where` 租户过滤可选（不强制） |
| `embedding_client.py` | 批量 embedding，按索引排序，正常 |
| `retriever.py` | 查询缓存（内存、无锁、满 100 清空）；**「hybrid」实为稠密余弦+词法融合**；`flashrank`/`rerank_model` 声明未用；`retrieval_min_score=0.45` 过滤，空则回退 top-k |
| `text_splitter.py` | 字符级切块（500/50），段落优先；无 token/语义感知，overlap 可能重复文本 |
| `ingest_service.py` | 正常编排：切块→元数据→入库 |

### 8.14 脚本 / 数据（`scripts/`、`data/`）

- ✅ `init_pg_memory_schema.sql`：幂等、部分索引、与代码一致。
- ✅ `restart.ps1` / `run_local.ps1`：端口清理 + 健康轮询。
- ⚠️ `data/` 下 `mock_checkpoint.py` / `mock_memoryStore.py` / `langsmith_demo.py` 不属于应用，`mock_memoryStore.py` 引用的 `langchain.messages` 等符号可能无法解析（未被打包，不影响运行，建议移出或归档）。

---

## 九、升级路线图（分阶段执行清单）

### 阶段 0：安全止血（P0，1 周内）8.11-8.11
- [ ] **4.1** `/admin/token` 接入真实凭证（密码哈希 + 校验；或改用 API Key）
- [ ] **4.1** 聊天/摄入/健康接口统一接入认证（可选：内网白名单 + TrustedHost）
- [ ] **4.2** `settings.py` 全部密钥默认值清空，强制环境变量读取，缺失即启动失败
- [ ] **4.2** 项目根添加 `.gitignore`（`.env`、`data/chroma_db/`、`*.sqlite3`、`__pycache__/`、`.venv/`）
- [ ] **4.2** 添加 `.env.example`；轮换已泄露的 DB/Redis/JWT 密钥
- [ ] **4.3** 修复 `AlertManager` 线程分发 Bug（线程安全队列 / `run_coroutine_threadsafe`）


### 阶段 1：生产能力（P1，2~3 周）

- [ ] **P1-1** 审计落盘：`audit.jsonl` 改为真正的 append-only 追加写；全动作埋点（chat/intervention/login/permission）；detail 脱敏
- [ ] **P1-2** 测试：补单元测试（mock 模型/DB）；集成测试接入 Testcontainers/本地模拟；配置 CI（GitHub Actions）
- [ ] **P1-3** 限流：覆盖全部端点；按租户/用户维度限流；ingest 增加大小/频率限制
- [ ] **P1-4** 多租户：`tenant_id/user_id` 改为必填或从 token 解析；检索 `where`、记忆 scope、缓存 key 强制带租户
- [ ] **P1-5** 连接管理：启用真实 PG 连接池；Redis 失败改为告警而非静默
- [ ] **P1-6** DLQ：接入后台重试 Worker（含指数退避）与重试 API；Redis key 加 TTL
- [ ] **P1-7** 人机协同：把 `create_intervention_request()` 接入 Agent 循环（工具失败/高严重度时触发）；修复 `finally` 覆盖真实结果的问题；修复 `execute_intervention()` 回调
- [ ] **P1-8** 动态配置：`_run_agent_loop`/`_execute_decomposed_tasks` 改为统一读 `ConfigManager`；`DynamicSettings` 加锁 + 持久化 + 值校验 + key 白名单
- [ ] **P1-9** 资源边界：`MetricsCollector` 加锁 + 有界淘汰；`get_summary` 按 request_id 过滤；捕获 `BudgetExceededError` 返回 429/400
- [ ] **P1-10** 线程安全：为共享状态统一加锁（或改为线程隔离/单写者）

### 阶段 2：能力增强（P2，3~6 周）

- [ ] 启用真实 rerank（flashrank / cross-encoder），hybrid 检索改为 BM25 + 稠密
- [ ] MCP：复用持久会话、加超时、客户端工具 allowlist、修复日志格式 Bug
- [ ] LLM：流式重试/降级、指数退避、LLM 层熔断
- [ ] 清理死代码：A2A 存根（或实现真实协议）、`passlib`、未用配置、`data/` 演示脚本归档
- [ ] 告警模型修正（`acknowledged_at` 默认 None）；接入真实通知渠道
- [ ] 多 Agent 真实化：为 `rag_agent`/`sql_agent` 等接入真实子 Agent 与专用工具集
- [ ] 异步化：Redis/psycopg 换异步驱动；`time.sleep` 退避改异步等待

### 阶段 3：生产加固（长期）

- [ ] 可观测：接入 Prometheus 指标导出 + Grafana 面板；结构化日志（JSON）
- [ ] 弹性：限流降级、全局熔断、优雅停机、健康检查增强（依赖探活）
- [ ] 安全：密钥托管（Vault/KMS）、敏感信息扫描（gitleaks/trivy）、依赖漏洞扫描
- [ ] 部署：容器化（Dockerfile）+ K8s/Compose + 配置中心
- [ ] 压测与容量规划：QPS/延迟/P99 指标基线、成本预算

---


## 附：关键文件索引

| 文件 | 作用 |
|---|---|
| `app/main.py` | 应用组装、依赖注入、启动探针 |
| `app/application/services/chat_service.py` | 核心编排（意图→工具→RAG→ReAct→任务分解→并行） |
| `app/config/settings.py` | 全量配置（含需整改的硬编码密钥） |
| `app/api/routers/admin.py` | 管理端点（含无凭证 token 签发） |
| `app/observability/alert_manager.py` | 告警管理（含线程 Bug） |
| `app/infrastructure/security/auth.py` | JWT/RBAC（仅 admin 生效） |
| `app/infrastructure/memory/redis_postgres_conversation_memory.py` | 双级记忆（连接池死代码） |
| `app/infrastructure/queue/dlq_handler.py` | 死信队列（只写不读） |
| `app/infrastructure/agent/intervention_handler.py` | 人机协同（未接线） |
| `app/rag/retrieval/retriever.py` | 检索（假 hybrid） |
| `tests/integration/test_chat_api.py` | 唯一测试（真实 API） |

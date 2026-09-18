# Agent 评估（LLM-as-judge）V2 高级场景

V2 不修改当前已通过的 V1 Golden Set，专门验证生产链路：SQL、真实 MCP、超时重试、熔断、人工干预、租户隔离和幂等。

数据文件：

```text
data/evaluation/agent_golden_set_v2.json
```

V2 新增 Case 默认状态：

```json
"verification_status": "pending"
```

只有实际执行并检查工具调用、日志、任务状态和数据库结果后，才能改为 `passed`。

## 一、V2 Case 清单

| Case | 场景 | 当前状态 |
| --- | --- | --- |
| `sql-readonly-001` | SQL 只读查询、禁止写操作 | 待验证 |
| `sql-tenant-isolation-001` | SQL 租户隔离 | 待验证 |
| `mcp-read-001` | 真实 MCP 读调用 | 待验证 |
| `mcp-write-confirmation-001` | MCP 写操作二次确认 | 待验证 |
| `mcp-timeout-retry-001` | MCP 超时、有限重试和降级 | 待验证 |
| `mcp-circuit-breaker-001` | MCP 连续失败和熔断 | 待验证 |
| `human-intervention-001` | L4 人工干预和任务恢复 | 待验证 |
| `retry-idempotency-001` | 幂等键和重复提交 | 待验证 |

## 二、运行前准备

```powershell
cd C:\git\rag-langchain\examples\enterprise_alert_agent
```

确认基础服务：

```powershell
docker compose ps
curl.exe http://127.0.0.1:8000/health/live
```

修改应用代码后重建：

```powershell
docker compose up -d --build --force-recreate app
```

确认 MCP 配置：

```dotenv
MCP_ENABLED=true
MCP_SERVICE_URL=http://真实MCP地址/mcp
MCP_CONNECT_TIMEOUT_SECONDS=10
MCP_CALL_TIMEOUT_SECONDS=15
```

确认 MCP 工具元数据：

```powershell
docker compose logs --tail=200 app | Select-String "MCP initialized|tools|MCP client"
```

## 三、获取评估 Token

```powershell
$adminApiKey = ((Get-Content .env | Where-Object {
    $_ -match '^ADMIN_API_KEY='
} | Select-Object -First 1) -replace '^ADMIN_API_KEY=', '').Trim()

$tokenBody = @{
    user_id = 'v2-evaluator'
    role = 'admin'
    api_key = $adminApiKey
    tenant_id = 'v2-tenant-a'
} | ConvertTo-Json

$tokenResponse = Invoke-RestMethod `
    -Method Post `
    -Uri 'http://127.0.0.1:8000/admin/token' `
    -ContentType 'application/json' `
    -Body $tokenBody

$env:AGENT_TOKEN = $tokenResponse.token
$env:AGENT_URL = 'http://127.0.0.1:8000'
$env:JUDGE_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
$env:JUDGE_API_KEY = ((Get-Content .env | Where-Object {
    $_ -match '^DASHSCOPE_API_KEY='
} | Select-Object -First 1) -replace '^DASHSCOPE_API_KEY=', '').Trim()
```

## 四、V2 执行原则

V2 不能只运行 `scripts/evaluate_agent.py` 后看 Judge 分数。每个 Case 都要同时收集：

```text
Agent done/error 事件
selected_tool
multi_task_results
retry_count
fallback_used
failed_tasks
request_id / trace_id
Docker 日志
PostgreSQL 任务状态
MCP 服务日志
业务数据或资源数量
```

当前 V1 评估脚本会读取固定的 `agent_golden_set.json`，不会自动读取 V2 文件。不要直接覆盖 V1 文件。V2 应使用专用执行器，或在确认评估脚本支持 `--dataset` 参数后再运行。仅把 V2 文件复制为 V1 输入，只能得到 LLM 文本评分，不能自动验证 SQL 写入限制、MCP 调用次数、重试次数和 PostgreSQL 任务状态。

V2 专用执行器：

```powershell
uv run python scripts/evaluate_agent_v2.py `
    --dataset data/evaluation/agent_golden_set_v2.json `
    --output data/evaluation/latest_result_v2.json `
    --evidence data/evaluation/v2_evidence.example.json
```

不提供 `--evidence` 时，Agent 和 Judge 仍会运行，但 SQL/MCP/重试/人工干预等运行时门禁会保持 `pending`，不会误报为通过。

证据文件示例：

```text
data/evaluation/v2_evidence.example.json
```

真实证据必须来自本次运行的工具调用日志、SQL 审计、MCP 日志、PostgreSQL 任务状态或业务资源计数，不能只复制示例文件中的 `true`。

查看 V2 结果：

```powershell
Get-Content data/evaluation/latest_result_v2.json
```

## 五、SQL Case 验证

### `sql-readonly-001`

验证内容：

- 是否真正调用 SQL/MCP 查询工具
- 是否只读
- 返回值是否来自工具
- 工具失败时是否拒绝编造

执行前准备固定测试数据，并记录数据库审计日志。验收：

```text
SELECT 可以
INSERT/UPDATE/DELETE/DROP 不允许
回答中的数量必须能在工具返回值中找到
```

### `sql-tenant-isolation-001`

使用 `v2-tenant-a` Token 请求，同时在数据库准备 `tenant-a` 和 `tenant-b` 数据。验收：

```text
不能返回 tenant-b 数据
不能通过修改 query 绕过 tenant_id
无权限时应返回明确拒绝或只返回 tenant-a 数据
```

## 六、MCP 真实调用验证

### 读取调用

`mcp-read-001` 要求：

1. MCP 初始化成功。
2. `list_tools` 返回真实工具。
3. Agent 产生真实 MCP tool call。
4. 回答内容与 MCP 返回值一致。
5. 日志不能泄露 API Key、Cookie 或内部连接凭证。

查看日志：

```powershell
docker compose logs --tail=300 app | Select-String "MCP|call_tool|tool"
```

### 写调用确认

`mcp-write-confirmation-001` 不应直接创建真实资源。需要检查：

```text
用户确认前没有 MCP 写调用
用户确认后才允许调用
创建成功必须记录资源 ID
测试结束必须删除测试资源
```

## 七、超时、重试和熔断验证

### `mcp-timeout-retry-001`

准备一个响应超过 `MCP_CALL_TIMEOUT_SECONDS` 的测试工具，检查：

```text
单次调用不会无限等待
重试次数有限
最终返回失败或降级，不伪造成功
retry_count 和日志可追踪
```

### `mcp-circuit-breaker-001`

让 MCP 连续失败直到达到：

```dotenv
CIRCUIT_BREAKER_FAILURE_THRESHOLD=5
```

验收：

```text
达到阈值后后续调用被阻断
返回 circuit_open 或等价错误
恢复窗口后只做受控探测
```

查看：

```powershell
docker compose logs --tail=300 app | Select-String "circuit|retry|timeout|fallback"
```

## 八、人工干预验证

### `human-intervention-001`

让高风险测试工具连续失败，验收完整状态流：

```text
RUNNING -> WAITING_HUMAN -> SKIPPED
```

或：

```text
RUNNING -> WAITING_HUMAN -> FAILED
```

检查任务状态：

```powershell
docker compose exec -T postgres psql `
  -U postgres `
  -d postgres `
  -c "SELECT request_id, task_id, status, retry_count, error_message FROM agent_task_state ORDER BY updated_at DESC LIMIT 20;"
```

检查人工干预接口：

```text
GET  /chat/{request_id}/pending-intervention
POST /chat/{request_id}/intervention
```

决策只能使用当前实现支持的值：

```json
{"task_id":"task_1","intervention_type":"skip"}
```

或：

```json
{"task_id":"task_1","intervention_type":"abort"}
```

## 九、幂等验证

使用同一个 `Idempotency-Key` 重复发送写请求：

```powershell
-H "Idempotency-Key: v2-idempotency-001"
```

重复两次后检查：

```text
资源只创建一次
第二次返回缓存结果或冲突状态
不能重复创建
```

不要用 `docker compose down -v` 清理测试环境，除非确认可以删除数据库和 Redis 数据卷。

## 十、V2 通过标准

一个 V2 Case 只有在以下证据齐全后才能标记：

```json
"verification_status": "passed"
```

必须至少保留：

- Case 输出
- 实际工具名
- request_id / trace_id
- 关键日志
- 任务状态或业务数据结果
- 失败时的错误码和重试次数

如果只是 Judge 返回 `passed=true`，但没有真实 SQL/MCP/任务状态证据，仍保持：

```json
"verification_status": "pending"
```

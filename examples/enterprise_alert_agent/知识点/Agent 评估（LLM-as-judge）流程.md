然后重建 App：
docker compose up -d --force-recreate app

等待健康：
curl.exe http://127.0.0.1:8000/health/live

验证文本是否恢复
docker compose exec app python -c "from app.config.settings import settings; import chromadb; client=chromadb.PersistentClient(path=settings.chroma_persist_directory); c=client.get_collection(settings.chroma_collection_name); r=c.get(include=['documents','metadatas']); print([x.encode('unicode_escape').decode() for x in r['documents']])"


最稳妥的 JSON 文件方式
$json = '{"content":"值班告警升级策略：连续3次失败后必须升级到二线。","source_id":"eval-doc-001","metadata":{"category":"eval"}}'

[System.IO.File]::WriteAllText(
    "$PWD\eval-ingest.json",
    $json,
    [System.Text.UTF8Encoding]::new($false)
)

使用 curl.exe 发送：
curl.exe `
    -X POST "http://127.0.0.1:8000/ingest/text" `
    -H "Authorization: Bearer $env:AGENT_TOKEN" `
    -H "Idempotency-Key: agent-eval-utf8-v3" `
    -H "Content-Type: application/json; charset=utf-8" `
    --data-binary "@eval-ingest.json"

再次验证：
docker compose exec app python -c "from app.config.settings import settings; import chromadb; client=chromadb.PersistentClient(path=settings.chroma_persist_directory); c=client.get_collection(settings.chroma_collection_name); print('COUNT=', c.count()); print(c.get(include=['documents','metadatas']))"

uv run python scripts/evaluate_agent.py


重新获取 Token 并写入样本
$adminApiKey = ((Get-Content .env | Where-Object {
    $_ -match "^ADMIN_API_KEY="
} | Select-Object -First 1) -replace "^ADMIN_API_KEY=", "").Trim()

$tokenBody = @{
    user_id = "local-evaluator"
    role = "admin"
    api_key = $adminApiKey
    tenant_id = "local-evaluation"
} | ConvertTo-Json

$tokenResponse = Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/admin/token" `
    -ContentType "application/json" `
    -Body $tokenBody

$env:AGENT_TOKEN = $tokenResponse.token




运行评估，不要按 Ctrl+C
uv run python scripts/evaluate_agent.py



当前两个 Case 调用一次可能需要几十秒，请等待完整输出
如果第一条仍失败
Get-Content data\evaluation\latest_result.json
如果仍然看到：
"selected_tool": ""
说明问题不再是评估脚本，而是 Agent 路由逻辑：当前 Agent 没有把该问题路由到 RAG 工具
DashScope Embedding：已通过
Agent 服务：已通过
JWT：已通过
Golden Set 入库：已通过
Judge 调用：已通过
Agent 评估结果：未通过
主要问题：RAG 路由/检索未命中

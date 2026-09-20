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


运行评估，不要按 Ctrl+C
uv run python scripts/evaluate_agent.py

修改代码的重建方式:
docker compose up -d --build --force-recreate app
docker compose build --no-cache app
docker compose up -d --force-recreate app


如果401 Unauthorized报错，操作:
cd C:\git\rag-langchain\examples\enterprise_alert_agent

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
$env:AGENT_URL = "http://127.0.0.1:8000"
$env:JUDGE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:JUDGE_API_KEY = ((Get-Content .env | Where-Object {
    $_ -match "^DASHSCOPE_API_KEY="
} | Select-Object -First 1) -replace "^DASHSCOPE_API_KEY=", "").Trim()

Write-Host "AGENT_TOKEN 已重新设置"

运行不同测试案例:
uv run python scripts/evaluate_agent.py

uv run python scripts/evaluate_agent_v2.py `
  --dataset data/evaluation/agent_golden_set_v2.json `
  --output data/evaluation/latest_result_v2.json `
  --evidence data/evaluation/v2_evidence.example.json
  
查看结果：
Get-Content data\evaluation\latest_result_v2.json

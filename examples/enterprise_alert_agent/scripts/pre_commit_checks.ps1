$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$testDataDir = Join-Path ([System.IO.Path]::GetTempPath()) "enterprise-alert-agent-pre-commit-$PID"
Push-Location $projectDir
try {
    uv run ruff check app tests
    uv run ruff format --check app tests
    uv run mypy app

    $env:APP_ENV = "test"
    $env:DASHSCOPE_API_KEY = "ci-placeholder"
    $env:ADMIN_JWT_SECRET = "ci-placeholder-admin-secret-32-characters"
    $env:MYSQL_PASSWORD = "ci-placeholder"
    $env:REDIS_PASSWORD = "ci-placeholder"
    $env:PG_PASSWORD = "ci-placeholder"
    $env:MODEL_STARTUP_PROBE_ENABLED = "false"
    $env:MCP_ENABLED = "false"
    $env:LANGSMITH_TRACING = "false"
    $env:PYTHONPATH = "."
    $env:CHROMA_PERSIST_DIRECTORY = $testDataDir

    uv run pytest -q tests/integration -k "health or validation"
}
finally {
    Remove-Item -Recurse -Force $testDataDir -ErrorAction SilentlyContinue
    Pop-Location
}

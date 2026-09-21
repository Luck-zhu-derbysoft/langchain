$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Split-Path -Parent $scriptDir)

$envFile = ".env"
if (Test-Path $envFile) {
	Write-Host "Using .env file"
} else {
	Write-Host "No .env found, copy from .env.example first"
}

if ([string]::IsNullOrWhiteSpace($env:DASHSCOPE_API_KEY)) {
	Write-Host "DASHSCOPE_API_KEY is not set in current shell; app will load it from .env if present."
} else {
	Write-Host "DASHSCOPE_API_KEY detected in current shell."
}

uv run python scripts/run_uvicorn.py

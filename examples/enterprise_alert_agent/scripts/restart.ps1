<#
.SYNOPSIS
    一键重启 Enterprise Alert Agent 应用（重新加载 .env 配置后启动）。

.DESCRIPTION
    1) 切换到项目根目录；
    2) 校验 .env 是否存在；
    3) 停止占用目标端口的旧进程（先尝试优雅关闭，超时则强制结束）；
    4) 重新启动 uvicorn 应用；
    5) 轮询 /health 直到服务就绪（或超时）。

.PARAMETER Reload
    启用 uvicorn --reload 热重载（开发模式）。默认不启用。

.PARAMETER Host
    绑定地址，默认 127.0.0.1。

.PARAMETER Port
    绑定端口，默认 8000。

.PARAMETER WaitSeconds
    等待服务就绪的超时秒数，默认 90 秒（首次导入 chromadb/连接远程 PG 较慢）。

.EXAMPLE
    .\scripts\restart.ps1
    普通模式重启，绑定 127.0.0.1:8000。

.EXAMPLE
    .\scripts\restart.ps1 -Reload
    开发模式重启，带热重载。

.EXAMPLE
    .\scripts\restart.ps1 -Host 0.0.0.0 -Port 8000 -WaitSeconds 120
#>
param(
    [switch]$Reload,
    [string]$HostAddr = "127.0.0.1",
    [int]$Port = 8000,
    [int]$WaitSeconds = 90
)

$ErrorActionPreference = "Stop"

# 1) 切换到项目根目录（脚本位于 scripts/ 下）
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
Set-Location $projectDir
Write-Host "==> 项目目录: $projectDir" -ForegroundColor Cyan

# 2) 校验 .env
$envFile = Join-Path $projectDir ".env"
if (-not (Test-Path $envFile)) {
    Write-Error "未找到 .env 文件，请先复制 .env.example 并配置后再重启。"
    exit 1
}
Write-Host "==> 使用配置文件: $envFile" -ForegroundColor Cyan

# 3) 停止旧进程（监听目标端口）
$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
    $pids = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($procId in $pids) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        Write-Host "==> 停止旧进程 PID=$procId ($($proc.ProcessName))..." -ForegroundColor Yellow
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
        } catch {
            Write-Host "    停止失败: $($_.Exception.Message)" -ForegroundColor Red
        }
    }
    Start-Sleep -Seconds 2
} else {
    Write-Host "==> 端口 $Port 无旧进程，跳过停止步骤" -ForegroundColor Green
}

# 4) 组装启动命令并启动应用
$uvicornArgs = @("run", "uvicorn", "app.main:app", "--host", $HostAddr, "--port", "$Port", "--log-level", "info")
if ($Reload) {
    $uvicornArgs += "--reload"
}
$cmdLine = "uv $($uvicornArgs -join ' ')"
Write-Host "==> 启动命令: $cmdLine" -ForegroundColor Cyan
Write-Host "==> 正在启动，首次启动需 30~60 秒导入依赖，请耐心等待..." -ForegroundColor Cyan

# 在独立终端窗口启动应用，避免本脚本被阻塞
$appWindowTitle = "Enterprise Alert Agent (port $Port)"
Start-Process -FilePath "uv" -ArgumentList $uvicornArgs -WorkingDirectory $projectDir `
    -WindowStyle Normal -Verb Open

# 5) 轮询 /health 直到服务就绪
$healthUrl = "http://$HostAddr`:$Port/health"
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$ready = $false
Write-Host "==> 等待服务就绪: $healthUrl" -ForegroundColor Cyan
while ((Get-Date) -lt $deadline) {
    try {
        $resp = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            $ready = $true
            Write-Host ""
            Write-Host "✅ 应用启动成功！" -ForegroundColor Green
            Write-Host "   地址: http://$HostAddr`:$Port" -ForegroundColor Green
            Write-Host "   接口文档: http://$HostAddr`:$Port/docs" -ForegroundColor Green
            Write-Host "   健康检查: $($resp.Content)" -ForegroundColor Green
            Write-Host "   (窗口标题: $appWindowTitle)" -ForegroundColor DarkGray
            break
        }
    } catch {
        # 服务还没就绪，继续轮询
    }
    Write-Host "   ...等待中（每 5 秒检查一次）" -ForegroundColor DarkGray
    Start-Sleep -Seconds 5
}

if (-not $ready) {
    Write-Host ""
    Write-Warning "等待 $WaitSeconds 秒后服务仍未就绪。"
    Write-Host "可能原因：导入依赖较慢 / 远程 PostgreSQL 不可达 / 端口被占用。"
    Write-Host "请检查启动窗口（标题: $appWindowTitle）中的日志，或直接访问: $healthUrl"
    exit 1
}

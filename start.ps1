# 一键启动 / 停止前后端。
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\start.ps1
#   powershell -ExecutionPolicy Bypass -File .\start.ps1 -Action stop
param(
    [ValidateSet("start", "stop")]
    [string]$Action = "start"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$BackendPort = 8000
$FrontendPort = 5173

function Stop-Listeners([int]$Port) {
    $ids = @()
    $lines = netstat -ano | Select-String -Pattern ":$Port\s"
    foreach ($line in $lines) {
        if ($line.Line -notmatch "LISTENING") { continue }
        $parts = ($line.Line.Trim() -split "\s+")
        $procId = $parts[-1]
        if ($procId -match "^\d+$" -and $procId -ne "0") {
            $ids += [int]$procId
        }
    }
    foreach ($procId in ($ids | Select-Object -Unique)) {
        Write-Host "[dev] 结束占用端口 $Port 的进程 PID=$procId"
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-DevProcesses {
    Stop-Listeners $BackendPort
    Stop-Listeners $FrontendPort
    # uvicorn --reload 残留的子进程（当前启动不用 --reload，这里只做清理）
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*uvicorn*" -or $_.CommandLine -like "*multiprocessing-fork*" } |
        ForEach-Object {
            Write-Host "[dev] 结束残留 Python PID=$($_.ProcessId)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Seconds 1
}

function Wait-Http([string]$Url, [int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
                return $true
            }
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }
    return $false
}

if ($Action -eq "stop") {
    Write-Host "[dev] 停止后端 :$BackendPort 与前端 :$FrontendPort"
    Stop-DevProcesses
    Write-Host "[dev] 已停止"
    exit 0
}

if (-not (Test-Path $Python)) {
    Write-Host "[dev] 找不到 $Python"
    Write-Host "[dev] 请先在 backend 目录创建虚拟环境并安装依赖："
    Write-Host "       python -m venv .venv"
    Write-Host "       .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Write-Host "[dev] 未找到 frontend\node_modules，正在 npm install ..."
    Push-Location $FrontendDir
    try {
        npm install
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path (Join-Path $BackendDir ".env"))) {
    Write-Host "[dev] 提示：backend\.env 不存在，后端可以启动，但模型调用未配置。"
    Write-Host "[dev]       复制 backend\.env.example 为 backend\.env 并填入 API Key。"
}

Write-Host "[dev] 清理已占用的 $BackendPort / $FrontendPort ..."
Stop-DevProcesses

$backendCmd = "chcp 65001 >nul & title AI创作-后端 & `"$Python`" -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort"
Start-Process -FilePath "cmd.exe" -WorkingDirectory $BackendDir -ArgumentList "/k", $backendCmd
Write-Host "[dev] 后端窗口已打开  http://127.0.0.1:$BackendPort"

$frontendCmd = "chcp 65001 >nul & title AI创作-前端 & npm run dev"
Start-Process -FilePath "cmd.exe" -WorkingDirectory $FrontendDir -ArgumentList "/k", $frontendCmd
Write-Host "[dev] 前端窗口已打开  http://127.0.0.1:$FrontendPort"

Write-Host "[dev] 等待服务就绪 ..."
$backendUp = Wait-Http "http://127.0.0.1:$BackendPort/api/health" 25
$frontendUp = Wait-Http "http://127.0.0.1:$FrontendPort" 25

if ($backendUp) {
    Write-Host "[dev] 后端已就绪"
} else {
    Write-Host "[dev] 后端尚未响应，请查看「AI创作-后端」窗口里的报错"
}
if ($frontendUp) {
    Write-Host "[dev] 前端已就绪，正在打开浏览器"
    Start-Process "http://127.0.0.1:$FrontendPort"
} else {
    Write-Host "[dev] 前端尚未响应，请查看「AI创作-前端」窗口里的报错"
}

Write-Host "[dev] 关闭对应窗口，或运行 stop.bat，即可停止服务"
exit 0

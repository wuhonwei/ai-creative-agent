#!/usr/bin/env bash
# 一键重启后端：清理占用 8000 端口的旧进程，再启动一个新的。
# 用法（在 backend 目录下）：./restart.sh
set -u

PORT="${1:-8000}"

echo "[restart] 查找占用端口 $PORT 的进程..."
# netstat 找到 LISTENING 的 PID（Windows）
PIDS=$(netstat -ano 2>/dev/null | grep ":$PORT " | grep 'LISTENING' | awk '{print $NF}' | sort -u)

if [ -n "$PIDS" ]; then
  for pid in $PIDS; do
    echo "[restart] 结束进程 PID=$pid"
    # uvicorn --reload 会有父子进程，先杀子再杀父；用 PowerShell 更可靠
    powershell -Command "Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue" 2>/dev/null || true
  done
  # 顺带清理可能残留的 multiprocessing 子进程
  powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*uvicorn*' -or \$_.CommandLine -like '*multiprocessing-fork*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }" 2>/dev/null || true
  sleep 3
else
  echo "[restart] 端口 $PORT 当前空闲"
fi

# 确认端口已释放
if netstat -ano 2>/dev/null | grep ":$PORT " | grep -q 'LISTENING'; then
  echo "[restart] 警告：端口 $PORT 仍被占用，可能需要手动处理或稍等几秒重试"
fi

echo "[restart] 启动后端 (http://127.0.0.1:$PORT) ..."
# 不用 --reload，避免父子进程难以清理
exec ./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"

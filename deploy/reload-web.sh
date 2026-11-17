#!/bin/sh
# 只重启网页进程：证书续期后 acme.sh 的 --reloadcmd 用这个。
# 桌面端直连的 server.py 不动，房间里的在线状态不会被打断。
set -u

APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
RUN_DIR="$APP_DIR/run"
pid_file="$RUN_DIR/web.pid"

if [ -f "$pid_file" ]; then
    pid=$(cat "$pid_file" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        i=0
        while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 20 ]; do
            sleep 0.2
            i=$((i + 1))
        done
    fi
fi

sh "$APP_DIR/deploy/start.sh"

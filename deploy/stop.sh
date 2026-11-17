#!/bin/sh
# 停止 pet-club 的两个进程
#
#   sh deploy/stop.sh
set -u

APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
RUN_DIR="$APP_DIR/run"

for name in web server; do
    pid_file="$RUN_DIR/$name.pid"
    if [ ! -f "$pid_file" ]; then
        echo "  $name 没有 PID 文件，跳过"
        continue
    fi

    pid=$(cat "$pid_file" 2>/dev/null)
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        echo "  $name 未在运行，清理 PID 文件"
        rm -f "$pid_file"
        continue
    fi

    kill "$pid" 2>/dev/null
    # 给它 3 秒体面退出，不结束就强杀
    i=0
    while [ "$i" -lt 30 ] && kill -0 "$pid" 2>/dev/null; do
        sleep 0.1
        i=$((i + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null
        echo "  $name 已强制停止（PID $pid）"
    else
        echo "  $name 已停止（PID $pid）"
    fi
    rm -f "$pid_file"
done

# PID 文件可能丢（手工起过、或上一版脚本没写），留下孤儿进程占着 80/443/8766。
# 按「工作目录 = 本目录」+ 脚本名精确清理，不碰机器上别的 Python 服务。
if command -v pgrep >/dev/null 2>&1; then
    for script in 'web_gateway\.py' 'server\.py'; do
        for pid in $(pgrep -f "$script" 2>/dev/null); do
            [ "$pid" = "$$" ] && continue
            cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
            [ "$cwd" = "$APP_DIR" ] || continue
            echo "  清理残留进程（PID $pid，$script）"
            kill "$pid" 2>/dev/null
            i=0
            while [ "$i" -lt 20 ] && kill -0 "$pid" 2>/dev/null; do
                sleep 0.1
                i=$((i + 1))
            done
            kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
        done
    done
fi

echo "== 已全部停止 =="

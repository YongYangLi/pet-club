#!/bin/sh
# pet-club 一键启动（CentOS 8 自带的 Python 3.6 就能跑，不需要 pip 装任何东西）
#
#   sh deploy/start.sh
#
# 会启动两个进程：
#   server.py      8765  桌面端直连的组队服务端（裸 TCP）
#   web_gateway.py 8766  网页版：HTTP 静态站 + WebSocket 桥
#
# PID 写到 run/，日志写到 logs/，重复执行不会起第二份。
set -u

APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$APP_DIR" || exit 1

LOG_DIR="$APP_DIR/logs"
RUN_DIR="$APP_DIR/run"
mkdir -p "$LOG_DIR" "$RUN_DIR"

PY=${PYTHON:-python3}
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "找不到 $PY，请先执行: yum install -y python3"
    exit 1
fi

SERVER_PORT=${SERVER_PORT:-8765}
WEB_PORT=${WEB_PORT:-8766}

start_one() {
    name=$1
    shift
    pid_file="$RUN_DIR/$name.pid"

    if [ -f "$pid_file" ]; then
        old=$(cat "$pid_file" 2>/dev/null)
        if [ -n "$old" ] && kill -0 "$old" 2>/dev/null; then
            echo "  $name 已在运行（PID $old），跳过"
            return 0
        fi
    fi

    # nohup + 重定向：退出 SSH 后进程继续活着
    nohup "$@" >> "$LOG_DIR/$name.log" 2>&1 &
    echo $! > "$pid_file"
    echo "  $name 已启动（PID $!），日志 logs/$name.log"
    return 0
}

echo "== pet-club 启动 =="
start_one server "$PY" server.py --host 0.0.0.0 --port "$SERVER_PORT"
sleep 1
start_one web "$PY" web_gateway.py --host 0.0.0.0 --port "$WEB_PORT" \
    --upstream "127.0.0.1:$SERVER_PORT"
sleep 1

# 起完立刻确认一下，别让「看起来启动了」骗过去
fail=0
for name in server web; do
    pid_file="$RUN_DIR/$name.pid"
    pid=$(cat "$pid_file" 2>/dev/null)
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        echo "  [失败] $name 没起来，最后几行日志："
        tail -n 20 "$LOG_DIR/$name.log" 2>/dev/null
        fail=1
    fi
done
if [ "$fail" -ne 0 ]; then
    echo "启动失败，看上面的日志"
    exit 1
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "$IP" ] || IP="<服务器公网IP>"

echo
echo "== 启动完成 =="
echo "  网页版：    http://$IP:$WEB_PORT/"
echo "  桌面端连：  $IP:$SERVER_PORT"
echo
echo "如果从外网还是打不开，检查防火墙："
echo "  firewall-cmd --add-port=$SERVER_PORT/tcp --add-port=$WEB_PORT/tcp --permanent"
echo "  firewall-cmd --reload"
echo "阿里云/腾讯云控制台的安全组也要放行 $SERVER_PORT、$WEB_PORT 两个 TCP 端口。"
echo
echo "停服务：sh deploy/stop.sh    看状态：sh deploy/status.sh"

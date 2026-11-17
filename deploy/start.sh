#!/bin/sh
# pet-club 一键启动（CentOS 8 自带的 Python 3.6 就能跑，不需要 pip 装任何东西）
#
#   sh deploy/start.sh
#
# 会启动两个进程：
#   server.py      8765  桌面端直连的组队服务端（裸 TCP）
#   web_gateway.py  80   网页版：HTTP 静态站 + WebSocket 桥
#
# 网关默认同时监听 80（给浏览器访问）和 8766（桌面端同步自定义素材）。
# 80 需要 root 权限；桌面端仍从 8766 拉素材，两个端口都要放行。
#
# 启用 HTTPS：把证书放到 certs/homie.pem + certs/homie.key，并设置
#   WEB_PORT=443 sh deploy/start.sh
# 网关会在 443 上跑 HTTPS，同时在 80 上做整站跳转（ACME 验证除外）。
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
WEB_EXTRA_PORT=${WEB_EXTRA_PORT:-8766}
# HTTPS（可选）：证书存在就启用；默认路径和 acme.sh 的安装目标一致。
WEB_TLS_CERT=${WEB_TLS_CERT:-$APP_DIR/certs/homie.pem}
WEB_TLS_KEY=${WEB_TLS_KEY:-$APP_DIR/certs/homie.key}
WEB_REDIRECT_PORT=${WEB_REDIRECT_PORT:-80}
ACME_WEBROOT=${ACME_WEBROOT:-$APP_DIR/acme}
mkdir -p "$ACME_WEBROOT"

WEB_TLS_ARGS=
WEB_SCHEME=http
if [ -f "$WEB_TLS_CERT" ] && [ -f "$WEB_TLS_KEY" ]; then
    WEB_TLS_ARGS="--cert $WEB_TLS_CERT --key $WEB_TLS_KEY"
    WEB_SCHEME=https
fi
# 有证书就默认 443（不用每次 SSH 都带 env，GitHub 部署也不会退回 80）。
if [ "$WEB_SCHEME" = "https" ]; then
    WEB_PORT=${WEB_PORT:-443}
else
    WEB_PORT=${WEB_PORT:-80}
fi
WEB_REDIRECT_ARGS=
if [ -n "$WEB_TLS_ARGS" ] && [ "$WEB_REDIRECT_PORT" != "0" ] \
        && [ "$WEB_REDIRECT_PORT" != "$WEB_PORT" ]; then
    WEB_REDIRECT_ARGS="--redirect-port $WEB_REDIRECT_PORT"
fi

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
    --extra-port "$WEB_EXTRA_PORT" --upstream "127.0.0.1:$SERVER_PORT" \
    --acme-webroot "$ACME_WEBROOT" $WEB_TLS_ARGS $WEB_REDIRECT_ARGS
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
if [ "$WEB_SCHEME" = "https" ] && [ "$WEB_PORT" = "443" ]; then
    echo "  网页版：    https://$IP/"
else
    echo "  网页版：    $WEB_SCHEME://$IP:$WEB_PORT/"
fi
echo "  桌面端连：  $IP:$SERVER_PORT"
echo "  素材同步：  $IP:$WEB_EXTRA_PORT（桌面端自定义素材走的端口）"
echo
echo "如果从外网还是打不开，检查防火墙："
echo "  firewall-cmd --add-port=$SERVER_PORT/tcp --add-port=$WEB_PORT/tcp --add-port=$WEB_EXTRA_PORT/tcp --permanent"
if [ "$WEB_SCHEME" = "https" ] && [ "$WEB_REDIRECT_PORT" != "0" ]; then
    echo "  firewall-cmd --add-port=$WEB_REDIRECT_PORT/tcp --permanent   # HTTP 自动跳转要用"
fi
echo "  firewall-cmd --reload"
echo "阿里云/腾讯云控制台的安全组也要放行 $SERVER_PORT、$WEB_PORT、$WEB_EXTRA_PORT 三个 TCP 端口。"
echo
echo "停服务：sh deploy/stop.sh    看状态：sh deploy/status.sh"

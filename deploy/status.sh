#!/bin/sh
# 看 pet-club 是否在跑、端口通不通
#
#   sh deploy/status.sh
set -u

APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$APP_DIR" || exit 1
RUN_DIR="$APP_DIR/run"
LOG_DIR="$APP_DIR/logs"

SERVER_PORT=${SERVER_PORT:-8765}
WEB_EXTRA_PORT=${WEB_EXTRA_PORT:-8766}
WEB_TLS_CERT=${WEB_TLS_CERT:-$APP_DIR/certs/homie.pem}
WEB_TLS_KEY=${WEB_TLS_KEY:-$APP_DIR/certs/homie.key}
WEB_SCHEME=http
if [ -f "$WEB_TLS_CERT" ] && [ -f "$WEB_TLS_KEY" ]; then
    WEB_SCHEME=https
fi
if [ "$WEB_SCHEME" = "https" ]; then
    WEB_PORT=${WEB_PORT:-443}
else
    WEB_PORT=${WEB_PORT:-80}
fi

echo "== 运行状态 =="
for name in server web; do
    pid_file="$RUN_DIR/$name.pid"
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "  $name: 运行中（PID $pid）"
            continue
        fi
    fi
    echo "  $name: 未运行"
done

echo
echo "== 端口 =="
for port in "$SERVER_PORT" "$WEB_PORT" "$WEB_EXTRA_PORT"; do
    if command -v ss >/dev/null 2>&1; then
        line=$(ss -lnt 2>/dev/null | awk -v p=":$port" '$4 ~ p"$" {print $4; exit}')
    else
        line=$(netstat -lnt 2>/dev/null | awk -v p=":$port" '$4 ~ p"$" {print $4; exit}')
    fi
    if [ -n "$line" ]; then
        echo "  $port: 监听中（$line）"
    else
        echo "  $port: 没有监听"
    fi
done

echo
echo "== 网页自检 =="
if command -v curl >/dev/null 2>&1; then
    # 本机用 127.0.0.1 访问，证书是发给域名的，这里跳过校验只看服务通不通。
    code=$(curl -ks -o /dev/null -w '%{http_code}' "$WEB_SCHEME://127.0.0.1:$WEB_PORT/" 2>/dev/null)
    echo "  GET $WEB_SCHEME://127.0.0.1:$WEB_PORT/ -> $code"
else
    echo "  没有 curl，跳过"
fi

echo
echo "== 最近日志 =="
for name in server web; do
    echo "--- logs/$name.log（最后 10 行）"
    tail -n 10 "$LOG_DIR/$name.log" 2>/dev/null || echo "  （还没有日志）"
done

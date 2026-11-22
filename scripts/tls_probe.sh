#!/usr/bin/env bash
# 探测某个主机名:443 能否被 Android 4.4（OpenSSL 1.0.1，TLS1.0/1.1 为主）
# 握手成功。通过 = 该源的播放域名大概率老盒子能播。
# 用法: tls_probe.sh <主机名> [port]
set -euo pipefail
HOST="${1:?用法: tls_probe.sh <主机名> [port]}"
PORT="${2:-443}"

echo "== $HOST:$PORT =="
for v in tls1 tls1_1 tls1_2; do
  out=$(echo | timeout 8 openssl s_client -connect "$HOST:$PORT" -"$v" -servername "$HOST" 2>/dev/null \
        | grep -E "Protocol|Cipher " | tr '\n' ' ')
  echo "  $v: ${out:-（握手失败）}"
done
echo "建议: 4.4 盒上选『至少 TLS1 或 TLS1_1 能协商』的域名最稳；只认 TLS1_2 的要实测。"

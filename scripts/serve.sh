#!/system/bin/sh
# Android 4.4 盒子上的单次 HTTP 应答器（busybox `nc` 版，无 toybox）。
# 用途：把 /sdcard/iptv/<文件> 用 HTTP 吐给盒内 App（http://127.0.0.1:<PORT>/）。
# 仅当你的播放器必须走 http 时才需要；TVBox Q 版用 clan:// 本地文件可跳过。
#
# 4.4 有 busybox 但没有 toybox，且 busybox 1.21 的 nc 不支持 -L（持久监听），
# 需用 `-l -l -e` 双 -l + -e 实现"每连接起一次本脚本"（xinetd 风格）。
# 启动（root/或 su）：
#   nohup busybox nc -l -p 8099 -l -e /data/local/tmp/iptv_serve.sh >/dev/null 2>&1 &
# 注意：重启后失效，尽量别依赖（见 docs/persistence.md）。

read -r req
REQ=$(echo "$req" | awk '{print $2}')
BASE=$(basename "$REQ")
F=/sdcard/iptv/$BASE

if [ -f "$F" ]; then
  LEN=$(wc -c < "$F")
  printf 'HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: %s\r\nConnection: close\r\nCache-Control: no-cache\r\n\r\n' "$LEN"
  cat "$F"
else
  printf 'HTTP/1.1 404 Not Found\r\nConnection: close\r\nContent-Length: 0\r\n\r\n'
fi
# 保持 socket 打开一会，避免局域网下客户端读到截断体。
sleep 1
exit 0

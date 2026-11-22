#!/usr/bin/env bash
# 一次性把 TVBox 直播源装到低版本盒子（本地文件方式，重启有效）。
# 用法: BOX=192.168.10.148:5555 ./box_live_setup.sh /你的源.m3u
#
# 说明: 只做直播。Q 版 TVBox 用 clan://localhost 本地文件规避老系统 TLS 问题，
#      不经此脚本下发任何点播/MacCMS 源。
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADB="${ADB:-$(command -v adb || echo adb)}"
BOX="${BOX:?请指定 BOX=<IP>:5555}"
SRC_M3U="${1:?用法: box_live_setup.sh <你的.m3u>}"
DST="/sdcard/iptv/live.txt"

python3 "$here/gen_live.py" "$SRC_M3U" "$here/live.txt"

"$ADB" connect "$BOX" >/dev/null 2>&1 || true
"$ADB" -s "$BOX" shell 'mkdir -p /sdcard/iptv'
"$ADB" -s "$BOX" push "$here/live.txt" "$DST"

cat <<EOF
完成。现在在电视上：
  TVBox → 设置 → 配置地址 → 直播 填: clan://localhost/$DST
或网页下发:
  curl -X POST -d 'do=liveApi' \\
    --data-urlencode 'url=clan://localhost/$DST' http://${BOX%:*}:9978/action
EOF

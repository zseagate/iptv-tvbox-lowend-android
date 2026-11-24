#!/usr/bin/env bash
# 低配盒子直播源一键刷新 —— 与高配仓库相同的生成方法:
#   merge_playlists.py (提取+去重+探活+死链过滤+黑名单+重分类+排序+校验)
#   -> check_live/loop/content + remove_loop (录播/循环剔除)
#   -> gen_live.py (m3u -> TVBox 直播 txt, 组,#genre# 格式)
#   -> 推送盒子 /sdcard/iptv/live.txt (TVBox Q 版 clan://localhost 本地文件)
#
# 用法: BOX=<IP>:5555 ./iptv-refresh-lowend.sh [--no-loop]
# Env: BOX, ADB, IPTV_OUT, IPTV_CACHE
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"
ADB="${ADB:-$(command -v adb || echo /home/mac/tools/platform-tools/adb)}"
BOX="${BOX:?请指定 BOX=<IP>:5555}"
PL="$repo/playlist"
export IPTV_OUT="${IPTV_OUT:-$PL}"
export IPTV_CACHE="${IPTV_CACHE:-$PL/probe-cache.json}"
export IPTV_UA="${IPTV_UA:-ExoPlayerLib/2.19.1}"
export IPTV_REFERER="${IPTV_REFERER:-auto}"
export PYTHONUNBUFFERED=1
DO_LOOP=1
[[ "${1:-}" == "--no-loop" ]] && DO_LOOP=0

[[ -f "$PL/box-dead.env" ]] && . "$PL/box-dead.env"

echo "[refresh] $(date '+%F %T') merge+probe+filter ..."
python3 "$PL/merge_playlists.py" 2>&1 | tail -6 || { echo "[refresh] merge failed, keep old" >&2; exit 0; }

if [[ "$DO_LOOP" == "1" ]]; then
  echo "[refresh] live/loop detection ..."
  python3 "$PL/check_live.py" "$PL/merged.m3u" "$PL/live-check.tsv" 2>/dev/null || true
  python3 "$PL/check_loop.py" "$PL/live-check.tsv" "$PL/loop-check.tsv" 2>/dev/null || true
  python3 "$PL/check_content.py" "$PL/loop-check.tsv" "$PL/content-check.tsv" 2>/dev/null || true
  python3 "$PL/remove_loop.py" "$PL/merged.m3u" "$PL/content-check.tsv" "$PL/merged.clean.m3u" 2>/dev/null || true
  [[ -s "$PL/merged.clean.m3u" ]] && mv "$PL/merged.clean.m3u" "$PL/merged.m3u"
fi

echo "[refresh] m3u -> TVBox live.txt ..."
python3 "$here/gen_live.py" "$PL/merged.m3u" "$here/live.txt"

echo "[refresh] push to $BOX ..."
"$ADB" connect "$BOX" >/dev/null 2>&1 || true
"$ADB" -s "$BOX" shell 'mkdir -p /sdcard/iptv' || true
"$ADB" -s "$BOX" push "$here/live.txt" /sdcard/iptv/live.txt || echo "[refresh] push skipped" >&2

echo "[refresh] done. TVBox 直播地址: clan://localhost/iptv/live.txt"

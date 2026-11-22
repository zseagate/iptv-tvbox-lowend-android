#!/usr/bin/env bash
# 探测一个 MacCMS 点播源对低版本盒子是否友好。
# 判定: ① API 返回合法 JSON（40+ 类目）② 播放地址是直链 m3u8 而非 /share/ 解析页
#       ③ 播放域名 TLS 1.2 可达（老盒子至少能谈 TLS1）
# 用法: vod_probe.sh <MacCMS baseUrl>
set -euo pipefail
URL="${1:?用法: vod_probe.sh <baseUrl> 例如 https://bfzyapi.com/api.php/provide/vod/}"

echo "== 探测 $URL =="
# ① API JSON
json=$(curl -sS -m 15 "$URL" 2>/dev/null || true)
if echo "$json" | grep -q '"list"'; then
  n=$(echo "$json" | grep -o '"vod_id"' | wc -l)
  echo "  [OK] API 返回 JSON，list 条目: $n"
else
  echo "  [FAIL] API 未返回合法 MacCMS JSON" && exit 1
fi

# ② 取第一条的播放地址
play=$(echo "$json" | grep -oE 'https?://[^"\\]+' | head -1 || true)
echo "  首条播放地址: ${play:-（无）}"
if [[ "${play:-}" == *"/share/"* ]]; then
  echo "  [WARN] 播放地址是 /share/ 解析页，老播放器需二次解析，可能播不了"
elif [[ "${play:-}" == *".m3u8" ]]; then
  echo "  [OK] 直链 m3u8，老盒子友好"
fi

# ③ 播放域名 TLS
if [[ -n "${play:-}" ]]; then
  host=$(echo "$play" | sed -E 's#https?://([^/]+)/.*#\1#')
  echo "  -- 播放域名 $host TLS:"
  echo | timeout 8 openssl s_client -connect "$host:443" -tls1_2 -servername "$host" 2>/dev/null \
    | grep -E "Protocol |Cipher " | tr '\n' ' ' && echo
fi
echo "结论: 建议用『直链 m3u8 + TLS1 可协商』的源（如暴风资源）。"

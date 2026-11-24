#!/usr/bin/env python3
"""从探活缓存剔除"可放宽原因"的死条目, 使放宽档下能重新探活。

仅移除那些因吞吐/时限门槛或瞬时网络超时被判死的 URL(放宽后可能转活);
保留真正死源(HTTP 4xx/5xx, ConnectionError, HTML, YouTube 等)不动, 省时间。
用法: python3 prune_cache_relaxable.py probe-cache.json
"""
import json
import re
import sys

# 可放宽的淘汰原因(命中则从缓存移除以便重探)
RELAXABLE = re.compile(
    r"too slow|tiny|media slow|ReadTimeout|ConnectTimeout|empty body|SSLError|"
    r"playlist without segments|m3u8 URL is not a playlist|"
    r"segment HTTP 40[0-9]|media .*KB|HTTP 429|HTTP 503",
    re.I,
)
# 明确不重探(真死/反爬)
KEEP_DEAD = re.compile(r"HTTP (4\d\d|5\d\d)|ConnectionError|HTML|YouTube|unsupported scheme|segment is", re.I)


def main() -> int:
    path = sys.argv[1]
    with open(path, encoding="utf-8") as fh:
        cache = json.load(fh)

    removed = []
    for url, rec in list(cache.items()):
        note = str(rec.get("note", ""))
        if rec.get("alive"):
            continue
        if KEEP_DEAD.search(note):
            continue
        if RELAXABLE.search(note):
            del cache[url]
            removed.append((url, note))

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh)
    print(f"pruned {len(removed)} relaxable-dead entries for re-probe")
    for url, note in removed[:40]:
        print(f"  {note[:40]:<40} {url[:80]}")
    if len(removed) > 40:
        print(f"  ... {len(removed)-40} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())

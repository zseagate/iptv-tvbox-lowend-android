#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m3u -> TVBox 直播 txt（`组,#genre#` 格式）。

TVBox "直播" 源（type 0）格式：

    分组名,#genre#
    频道名,http(s)://<stream>

从 `#EXTINF` 的 `group-title="..."` 取分组，显示名取频道名。
输出确定性排序（分组固定顺序 + 组内字母序），定时刷新时 diff 最小。

用法:
    python3 gen_live.py <输入.m3u> [输出.txt]
    默认输出 ./live.txt

本脚本不含任何真实源；输入是你自己的 m3u 清单。
"""
import io
import re
import sys
import os

# 常见分组优先靠前；未知分组按名字排在末尾。可自行增删。
GROUP_ORDER = [
    "央视频道", "卫视频道", "港澳台频道", "新闻频道", "电影频道", "体育频道",
    "少儿动漫", "动漫片", "纪录人文", "综艺频道", "音乐频道", "教育频道",
    "文旅频道", "生活频道", "其他频道", "海外频道",
]


def rank(group):
    fam = group.split("·")[0]
    if fam in GROUP_ORDER:
        return (GROUP_ORDER.index(fam), group)
    return (len(GROUP_ORDER), group)


def parse(path):
    rows, cur = [], None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#EXTINF"):
                g = re.search(r'group-title="([^"]*)"', line)
                name = line.rsplit(",", 1)[-1].strip()
                cur = (g.group(1) if g else "其他频道", name)
            elif line.startswith("#"):
                continue
            elif line.strip() and cur:
                rows.append((cur[0], cur[1], line.strip()))
                cur = None
    return rows


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: python3 gen_live.py <输入.m3u> [输出.txt]")
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else "live.txt"
    if not os.path.isfile(src):
        sys.exit("找不到输入 m3u: %s" % src)

    rows = parse(src)
    seen, uniq = set(), []
    for g, n, u in rows:              # 按 (组,名) 去重，先出现者优先
        if (g, n) in seen:
            continue
        seen.add((g, n))
        uniq.append((g, n, u))

    groups = {}
    for g, n, u in uniq:
        groups.setdefault(g, []).append((n, u))
    for g in groups:
        groups[g].sort(key=lambda x: x[0].lower())

    out = io.StringIO()
    for g in sorted(groups, key=rank):
        out.write("%s,#genre#\n" % g)
        for n, u in groups[g]:
            out.write("%s,%s\n" % (n, u))

    with open(dst, "w", encoding="utf-8") as f:
        f.write(out.getvalue())
    print("gen_live: %d 频道 / %d 组 -> %s" % (len(uniq), len(groups), dst))


if __name__ == "__main__":
    main()

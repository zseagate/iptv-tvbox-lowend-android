#!/usr/bin/env python3
"""Distinguish true looping/recorded HLS from live-with-fixed-sequence.

A live stream advances its segment list even when #EXT-X-MEDIA-SEQUENCE
stays constant (some providers keep the sequence fixed and roll the
playlist).  A truly looping/recorded feed returns the SAME segment list
on repeated fetches.

Usage: python3 check_loop.py <same-urls.tsv> <out.tsv>
"""
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

UA = "VLC/3.0.20 LibVLC/3.0.20"
TIMEOUT = (5, 10)
GAP = 30  # long enough for a live playlist to roll


def segs_of(url: str) -> tuple[int | None, list[str]]:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        if r.status_code not in (200, 206):
            return None, []
        seq = None
        segs = []
        for line in r.text.splitlines():
            line = line.strip()
            m = re.match(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", line)
            if m:
                seq = int(m.group(1))
            elif not line.startswith("#") and line:
                segs.append(line.split("?")[0])  # strip query for stable compare
        return seq, segs
    except Exception:
        return None, []


def classify(url: str) -> tuple[str, str, list[str]]:
    s1, g1 = segs_of(url)
    time.sleep(GAP)
    s2, g2 = segs_of(url)
    # compare segment lists (long gap: a live feed rolls its segments)
    if g1 and g2:
        if g1 == g2:
            return "loop-same", f"segs identical after {GAP}s ({len(g1)})", g1
        return "live-rolling", f"segs rolled ({len(g1)}->{len(g2)})", g1
    if s1 is not None and s2 is not None:
        return ("live-seq" if s2 > s1 else "same-seq"), f"seq {s1}->{s2}", g1
    return "unreadable", "no segs", g1


def main() -> int:
    src = sys.argv[1]
    out = sys.argv[2]
    urls = []
    with open(src) as fh:
        next(fh)
        for line in fh:
            u, v, d = line.rstrip("\n").split("\t")
            if v == "same":
                urls.append(u)
    print(f"same-urls to recheck: {len(urls)}")
    results = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = {pool.submit(classify, u): u for u in urls}
        for fut in as_completed(futs):
            u = futs[fut]
            try:
                v, d, segs = fut.result()
            except Exception as e:
                v, d, segs = "error", type(e).__name__, []
            results.append((u, v, d))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("url\tverdict\tdetail\n")
        for u, v, d in results:
            fh.write(f"{u}\t{v}\t{d}\n")
    from collections import Counter
    cnt = Counter(v for _, v, _ in results)
    print("verdicts:", dict(cnt))
    for u, v, d in results:
        if v in ("loop-same", "same-seq"):
            print(f"  [LOOP] {v} {d}  {u}")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

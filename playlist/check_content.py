#!/usr/bin/env python3
"""Content-level verification of suspected looping/recorded HLS.

For each URL, download segment[0] content, wait, re-download the same
segment.  If the bytes are identical the stream replays the same content
(a true loop / recording); if different it is live.

Usage: python3 check_content.py <loop.tsv> <out.tsv>
"""
import hashlib
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

UA = {"User-Agent": "VLC/3.0.20 LibVLC/3.0.20"}
TIMEOUT = (6, 12)
GAP = 5


def sha(url: str) -> tuple[str, int]:
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        return hashlib.md5(r.content).hexdigest()[:16], len(r.content)
    except Exception as e:
        return "ERR:" + type(e).__name__, 0


def first_seg(url: str) -> str | None:
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        for line in r.text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return urllib.parse.urljoin(r.url, line)
    except Exception:
        pass
    return None


def classify(url: str) -> tuple[str, str]:
    seg = first_seg(url)
    if not seg:
        return "no-seg", "playlist empty/unreadable"
    h1, n1 = sha(seg)
    if h1.startswith("ERR"):
        return "seg-err", f"{h1}"
    time.sleep(GAP)
    h2, n2 = sha(seg)
    if h2.startswith("ERR"):
        return "seg-err2", f"{h1}->{h2}"
    if h1 == h2:
        return "loop-confirm", f"seg identical md5={h1} ({n1}B)"
    return "live-content", f"seg changed {h1}->{h2}"


def main() -> int:
    src = sys.argv[1]
    out = sys.argv[2]
    urls = []
    with open(src) as fh:
        next(fh)
        for line in fh:
            u, v, d = line.rstrip("\n").split("\t")
            if v == "loop-same":
                urls.append(u)
    print(f"loop-same to content-verify: {len(urls)}")
    results = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(classify, u): u for u in urls}
        done = 0
        for fut in as_completed(futs):
            u = futs[fut]
            try:
                v, d = fut.result()
            except Exception as e:
                v, d = "error", type(e).__name__
            results.append((u, v, d))
            done += 1
            if done % 20 == 0:
                print(f"  {done}/{len(urls)}", flush=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("url\tverdict\tdetail\n")
        for u, v, d in results:
            fh.write(f"{u}\t{v}\t{d}\n")
    from collections import Counter
    cnt = Counter(v for _, v, _ in results)
    print("verdicts:", dict(cnt))
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

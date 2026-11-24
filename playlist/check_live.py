#!/usr/bin/env python3
"""Check whether m3u8 channels are truly LIVE or looping/recorded.

For each m3u8 URL, fetch the media-sequence twice (3s apart).  A live
stream advances its sequence; a looping/recorded feed stays fixed (or
wraps back to an earlier value).  Report non-advancing channels.

Usage: python3 check_live.py [input.m3u] [out.tsv]
"""
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

UA = "VLC/3.0.20 LibVLC/3.0.20"
TIMEOUT = (5, 8)
GAP = 3  # seconds between the two sequence samples


def seq_of(url: str) -> int | None:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        if r.status_code not in (200, 206):
            return None
        for line in r.text.splitlines():
            m = re.match(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", line.strip())
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return None


def classify(url: str) -> tuple[str, str]:
    s1 = seq_of(url)
    time.sleep(GAP)
    s2 = seq_of(url)
    if s1 is None or s2 is None:
        return "unreadable", f"{s1}/{s2}"
    if s2 > s1:
        return "live", f"{s1}->{s2}"
    if s2 == s1:
        return "same", f"{s1}=={s2}"
    return "rewind", f"{s1}->{s2}"


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else "../out/merged.m3u"
    out = sys.argv[2] if len(sys.argv) > 2 else "../out/live-check.tsv"
    urls: list[str] = []
    seen = set()
    with open(src, encoding="utf-8") as fh:
        cur = None
        for raw in fh:
            line = raw.strip()
            if line.startswith("#EXTINF"):
                cur = line
            elif not line.startswith("#") and line and cur:
                if ".m3u8" in line and line not in seen:
                    seen.add(line)
                    urls.append(line)
                cur = None
    print(f"m3u8 channels to check: {len(urls)}")

    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=16) as pool:
        futs = {pool.submit(classify, u): u for u in urls}
        done = 0
        for fut in as_completed(futs):
            u = futs[fut]
            try:
                verdict, detail = fut.result()
            except Exception as e:
                verdict, detail = "error", type(e).__name__
            results.append((u, verdict, detail))
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(urls)} ({time.time()-t0:.0f}s)", flush=True)

    with open(out, "w", encoding="utf-8") as fh:
        fh.write("url\tverdict\tdetail\n")
        for u, v, d in results:
            fh.write(f"{u}\t{v}\t{d}\n")

    from collections import Counter
    cnt = Counter(v for _, v, _ in results)
    print("verdicts:", dict(cnt))
    print(f"non-live: {sum(cnt.get(k,0) for k in ('same','rewind','unreadable'))}")
    for u, v, d in results:
        if v in ("same", "rewind"):
            print(f"  [{v}] {d}  {u}")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

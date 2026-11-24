#!/usr/bin/env python3
"""Probe IPTV streams and publish a filtered M3U.

Environment:
  IPTV_OUT       output directory            (default: ../out)
  IPTV_CACHE     probe cache json            (default: $IPTV_OUT/probe-cache.json)
  IPTV_SNAPSHOT  local snapshot m3u          (default: ../best_sorted.m3u)
  IPTV_LIVE_URL  upstream playlist URL       (default: jsDelivr Collect-IPTV)

Outputs: best_sorted.m3u (filtered upstream), snapshot_alive.m3u, report.txt
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
import urllib3

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
OUT = os.environ.get("IPTV_OUT", os.path.join(ROOT, "out"))
CACHE = os.environ.get("IPTV_CACHE", os.path.join(OUT, "probe-cache.json"))
SNAPSHOT = os.environ.get("IPTV_SNAPSHOT", os.path.join(ROOT, "best_sorted.m3u"))
LIVE_URL = os.environ.get(
    "IPTV_LIVE_URL",
    "https://cdn.jsdelivr.net/gh/zilong7728/Collect-IPTV@main/best_sorted.m3u",
)

UA = os.environ.get("IPTV_UA", "VLC/3.0.20 LibVLC/3.0.20")
# "auto" sends the stream's own origin as Referer (scheme://host/)
REFERER = os.environ.get("IPTV_REFERER", "")
READ_EXC = (requests.RequestException, urllib3.exceptions.HTTPError, OSError)
TIMEOUT = tuple(int(x) for x in os.environ.get("IPTV_TIMEOUT", "5,8").split(","))  # connect, read
SEG_TIMEOUT = tuple(int(x) for x in os.environ.get("IPTV_SEG_TIMEOUT", "5,6").split(","))
REACH_TIMEOUT = tuple(int(x) for x in os.environ.get("IPTV_REACH_TIMEOUT", "5,6").split(","))
WORKERS = int(os.environ.get("IPTV_WORKERS", "32"))
MIN_BYTES = 1024
# real-throughput gate: a stream must deliver this much data, at this speed,
# within the deadline, otherwise it would stutter on the box.
# All env-overridable so strictness can be relaxed (see README 探活可放宽点):
#   IPTV_READ_DEADLINE  秒, 分段下载期限 (默认 8.0)
#   IPTV_MIN_SPEED_BPS  最低吞吐, 未读完时要求 (默认 32KB/s)
#   IPTV_MIN_ALIVE_BYTES 直链媒体最小字节 (默认 128KB)
#   IPTV_MIN_CHUNK_BYTES 未读完时最小字节 (默认 512KB)
#   IPTV_MIN_TINY_BYTES  读完时最小字节 (默认 32KB)
READ_BUDGET_BYTES = 256 * 1024
READ_DEADLINE = float(os.environ.get("IPTV_READ_DEADLINE", "8.0"))
MIN_SPEED_BPS = int(os.environ.get("IPTV_MIN_SPEED_BPS", str(32 * 1024)))
MIN_ALIVE_BYTES = int(os.environ.get("IPTV_MIN_ALIVE_BYTES", str(128 * 1024)))
MIN_CHUNK_BYTES = int(os.environ.get("IPTV_MIN_CHUNK_BYTES", str(512 * 1024)))
MIN_TINY_BYTES = int(os.environ.get("IPTV_MIN_TINY_BYTES", str(32 * 1024)))
PROBE_DEPTH = int(os.environ.get("IPTV_PROBE_DEPTH", "3"))
CACHE_TTL = int(os.environ.get("IPTV_CACHE_TTL", str(6 * 3600)))
SKIP_HOSTS = ("youtube.com", "youtu.be", "googlevideo.com") + tuple(
    h for h in os.environ.get("IPTV_SKIP_HOSTS", "").replace(";", ",").split(",") if h
)


def headers_for(url: str) -> dict:
    h = {"User-Agent": UA}
    if REFERER == "auto":
        from urllib.parse import urlsplit
        p = urlsplit(url)
        if p.scheme and p.netloc:
            h["Referer"] = f"{p.scheme}://{p.netloc}/"
    elif REFERER:
        h["Referer"] = REFERER
    return h


def read_budget(resp, budget: int, deadline: float) -> tuple[bytes, float]:
    """Read until budget/deadline; returns (bytes, elapsed)."""
    start = time.monotonic()
    buf = b""
    while len(buf) < budget:
        left = deadline - (time.monotonic() - start)
        if left <= 0:
            break
        chunk = resp.raw.read(min(65536, budget - len(buf)), decode_content=True)
        if not chunk:
            break
        buf += chunk
    return buf, time.monotonic() - start


def encode_url(url: str) -> str:
    """Percent-encode non-ASCII URL parts (Android's Uri parser chokes on raw CJK)."""
    if url.isascii():
        return url
    from urllib.parse import quote, urlsplit, urlunsplit
    p = urlsplit(url)
    path = quote(p.path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(p.query, safe="=&%:@!$'()*+,;/?-._~")
    return urlunsplit((p.scheme, p.netloc, path, query, p.fragment))


def parse_m3u(text: str) -> list[dict]:
    entries: list[dict] = []
    extinf = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            extinf = raw.strip()
        elif line.startswith("#"):
            continue
        elif extinf is not None:
            entries.append({"extinf": extinf, "url": encode_url(line)})
            extinf = None
    return entries


def load_cache() -> dict:
    try:
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh)
    os.replace(tmp, CACHE)


def looks_like_html(ctype: str, body: bytes) -> bool:
    if "text/html" in ctype.lower():
        return True
    head = body[:512].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


def _extinf_duration(line: str) -> float | None:
    try:
        return float(line.split(":", 1)[1].split(",", 1)[0])
    except Exception:
        return None


def _fetch(url: str, timeout=TIMEOUT):
    return requests.get(url, headers=headers_for(url), timeout=timeout,
                        stream=True, allow_redirects=True)


def _read_body(resp, limit: int = 65536) -> bytes:
    return resp.raw.read(limit, decode_content=True) or b""


def _first_segment(text: str) -> tuple[str | None, float | None]:
    dur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            dur = _extinf_duration(line)
        elif not line.startswith("#"):
            return line, dur
    return None, None


def _probe_media(url: str, duration: float | None, depth: int) -> tuple[bool, str]:
    """Download one segment and require it to finish well within its play time."""
    deadline = max(2.0, duration * 0.8) if duration else READ_DEADLINE
    start = time.monotonic()
    got = 0
    complete = False
    try:
        with _fetch(url, SEG_TIMEOUT) as r:
            if r.status_code not in (200, 206):
                return False, f"segment HTTP {r.status_code}"
            ctype = r.headers.get("content-type", "")
            first = r.raw.read(1024, decode_content=True) or b""
            if looks_like_html(ctype, first):
                return False, "segment is HTML"
            if first.lstrip().startswith(b"#EXTM3U"):
                return False, "segment is another playlist"
            got = len(first)
            while got < 2 * 1024 * 1024:
                left = deadline - (time.monotonic() - start)
                if left <= 0:
                    break
                chunk = r.raw.read(65536, decode_content=True)
                if not chunk:
                    complete = True
                    break
                got += len(chunk)
            elapsed = time.monotonic() - start
    except READ_EXC as exc:
        return False, type(exc).__name__

    speed = got / elapsed if elapsed else 0
    if not complete:
        # reached the size cap: that's fine as long as we got a healthy chunk
        # and the speed is acceptable (live segments can be huge / endless)
        if got < MIN_CHUNK_BYTES or speed < MIN_SPEED_BPS:
            return False, f"too slow {got//1024}KB in {deadline:.1f}s ({speed/1024:.0f}KB/s)"
    if got < MIN_TINY_BYTES:
        return False, f"tiny {got//1024}KB"
    return True, f"segment {got//1024}KB in {elapsed:.1f}s ({speed/1024:.0f}KB/s)"


def probe(url: str) -> tuple[bool, str]:
    """Return (alive, note); follows nested HLS playlists up to 3 levels.

    Alive means the media can actually be fetched and parsed the way the box's
    player (ExoPlayer) will fetch it:
      * only http(s) URLs,
      * a .m3u8 URL must really return a playlist,
      * segments must be real media (not HTML, not another playlist, not YouTube),
      * a segment must download faster than it plays (otherwise it stutters).
    """
    from urllib.parse import urlsplit

    def scheme_ok(u: str) -> bool:
        return urlsplit(u).scheme in ("http", "https")

    if not scheme_ok(url):
        return False, f"unsupported scheme {urlsplit(url).scheme}"

    cur = url
    duration = None
    try:
        for depth in range(PROBE_DEPTH):
            with _fetch(cur) as r:
                if r.status_code not in (200, 206):
                    return False, f"HTTP {r.status_code}"
                ctype = r.headers.get("content-type", "")
                body = _read_body(r)
                path = urlsplit(cur).path.lower()
                if looks_like_html(ctype, body):
                    return False, "HTML error page"
                if not body:
                    return False, "empty body"
                if not body.lstrip().startswith(b"#EXTM3U"):
                    if path.endswith((".m3u8", ".m3u")):
                        return False, "m3u8 URL is not a playlist"
                    # direct media stream (TS/MP4): keep reading this response
                    start = time.monotonic()
                    got = len(body)
                    while got < 1024 * 1024:
                        left = READ_DEADLINE - (time.monotonic() - start)
                        if left <= 0:
                            break
                        chunk = r.raw.read(65536, decode_content=True)
                        if not chunk:
                            break
                        got += len(chunk)
                    elapsed = time.monotonic() - start
                    speed = got / elapsed if elapsed else 0
                    if got < MIN_ALIVE_BYTES:
                        return False, f"media slow {got//1024}KB in {elapsed:.1f}s"
                    if speed < MIN_SPEED_BPS:
                        return False, f"media {speed/1024:.0f}KB/s"
                    return True, f"media {ctype or 'unknown'} {got//1024}KB ({speed/1024:.0f}KB/s)"
                text = body.decode("utf-8", "replace")
                seg, duration = _first_segment(text)
                if not seg:
                    return False, "playlist without segments"
                cur = urljoin(r.url, seg)
                if not scheme_ok(cur):
                    return False, "segment uses non-HTTP scheme"
                if any(h in cur for h in SKIP_HOSTS):
                    return False, "segment points at YouTube"
                if "#EXT-X-STREAM-INF" not in text:
                    # media playlist: measure the segment against its play time
                    return _probe_media(cur, duration, depth)
        return _probe_media(cur, duration, 0)
    except READ_EXC as exc:
        return False, type(exc).__name__


def reachable(url: str) -> tuple[bool, str]:
    """Cheap reachability test: fetch a tiny prefix, require a media response.

    Used as a pre-filter so the expensive full probe only runs on hosts that
    actually answer; otherwise batch probing 1000s of streams saturates the
    uplink and produces false 'too slow' verdicts.
    """
    try:
        with _fetch(url, REACH_TIMEOUT) as r:
            if r.status_code not in (200, 206):
                return False, f"HTTP {r.status_code}"
            first = r.raw.read(1024, decode_content=True) or b""
            ctype = r.headers.get("content-type", "")
            if looks_like_html(ctype, first):
                return False, "HTML error page"
            if not first:
                return False, "empty body"
            return True, "reachable"
    except READ_EXC as exc:
        return False, type(exc).__name__



def write_m3u(path: str, header: list[str], entries: list[dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n")
        for line in header:
            fh.write(line.rstrip() + "\n")
        for e in entries:
            fh.write(e["extinf"] + "\n")
            fh.write(e["url"] + "\n")
    os.replace(tmp, path)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    cache = load_cache()
    now = time.time()

    print(f"[{datetime.now():%F %T}] fetching upstream ...")
    live_text = requests.get(LIVE_URL, headers={"User-Agent": UA},
                             timeout=(8, 20)).text
    snap_text = open(SNAPSHOT, encoding="utf-8").read()
    live = parse_m3u(live_text)
    snap = parse_m3u(snap_text)
    print(f"upstream={len(live)} channels, snapshot={len(snap)} channels")

    urls, seen = [], set()
    for e in live + snap:
        if e["url"] not in seen:
            seen.add(e["url"])
            urls.append(e["url"])

    todo, results = [], {}
    for u in urls:
        c = cache.get(u)
        if c and now - c.get("ts", 0) < CACHE_TTL:
            results[u] = c
        else:
            todo.append(u)
    print(f"unique urls={len(urls)} cached={len(results)} probing={len(todo)}")

    started, done = time.time(), 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for url, (alive, note) in zip(todo, pool.map(probe, todo)):
            results[url] = {"alive": alive, "note": note, "ts": time.time()}
            done += 1
            if done % 25 == 0:
                print(f"  probed {done}/{len(todo)} ({time.time()-started:.0f}s)")

    cache.update(results)
    save_cache(cache)

    live_alive = [e for e in live if results.get(e["url"], {}).get("alive")]
    snap_alive = [e for e in snap if results.get(e["url"], {}).get("alive")]

    gen = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S %Z")
    header = [
        f"# Filtered-Time: {gen}",
        f"# Source: {LIVE_URL}",
        "# Filter: every stream probed; dead entries removed",
        f"# Channel-Count: {len(live_alive)}",
        "#EXTVLCOPT:http-user-agent=VLC/3.0.20 LibVLC/3.0.20",
    ]
    write_m3u(os.path.join(OUT, "best_sorted.m3u"), header, live_alive)
    write_m3u(os.path.join(OUT, "snapshot_alive.m3u"), header, snap_alive)

    with open(os.path.join(OUT, "report.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"generated: {gen}\n")
        fh.write(f"upstream: {len(live)} channels, alive {len(live_alive)}\n")
        fh.write(f"snapshot: {len(snap)} channels, alive {len(snap_alive)}\n\n")
        fh.write("STATUS\tURL\tNOTE\n")
        for e in live:
            r = results.get(e["url"], {})
            fh.write(("OK" if r.get("alive") else "DEAD") + "\t" + e["url"] + "\t" + str(r.get("note", "")) + "\n")

    print(f"upstream alive={len(live_alive)}/{len(live)}  snapshot alive={len(snap_alive)}/{len(snap)}")
    print(f"written: {OUT}/best_sorted.m3u")
    return 0


if __name__ == "__main__":
    sys.exit(main())

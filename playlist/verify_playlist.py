#!/usr/bin/env python3
"""Re-check a finished playlist the way ExoPlayer will load it and drop bad entries.

TiviMate runs ExoPlayer, which shows ``ParserException`` when a URL that should be
HLS is not HLS, when the manifest has no usable segment, or when the segment body
is not real media (HTML page, JSON error, YouTube link, another playlist, ...).

This pass walks every entry to its first media segment and checks the payload
magic bytes (TS / fMP4 / MP4 / FLV / WebM / ADTS / MP3). Entries that fail are
removed and the playlist is rewritten atomically.

Environment (same as filter_playlist.py):
  IPTV_UA, IPTV_REFERER, IPTV_OUT

Usage:
  python3 verify_playlist.py [playlist.m3u]
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import requests

from filter_playlist import (OUT, READ_EXC, SEG_TIMEOUT, _fetch, _first_segment,
                             _read_body, headers_for, looks_like_html, parse_m3u)

READ_LIMIT = 128 * 1024  # enough for container magic; keeps the pass light
SEG_DEADLINE = 8.0


def media_kind(buf: bytes, ctype: str = "") -> str | None:
    """Return a short media kind when buf starts with a known container."""
    if len(buf) < 16:
        return None
    ct = (ctype or "").lower()
    # MPEG-TS: 0x47 sync every 188 (or 192 for M2TS)
    head = buf[:600]
    for stride in (188, 192):
        if len(head) > stride * 2 and head[0] == 0x47 and head[stride] == 0x47 and head[stride * 2] == 0x47:
            return "ts"
    # ISO-BMFF (fMP4 / MP4)
    for box in (b"ftyp", b"styp", b"moof", b"moov", b"mdat", b"free", b"skip", b"emsg", b"sidx"):
        if buf[4:8] == box:
            return "mp4"
    if buf[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if buf[:3] == b"FLV":
        return "flv"
    if buf[:3] == b"ID3":
        return "mp3"
    # ADTS AAC: 12-bit sync 0xFFF and layer bits 00
    if len(buf) >= 7 and buf[0] == 0xFF and (buf[1] & 0xF6) == 0xF0:
        return "aac"
    if buf[0] == 0xFF and (buf[1] & 0xE0) == 0xE0:
        return "mp3"
    if "video/mp2t" in ct or "mpeg" in ct or "mp2t" in ct:
        return "ts"
    if "video/mp4" in ct or "audio/mp4" in ct:
        return "mp4"
    if "audio/aac" in ct:
        return "aac"
    if "audio/mpeg" in ct:
        return "mp3"
    return None


def _manifest_key_url(text: str, base: str) -> str | None:
    """First EXT-X-KEY URI, or '' when the key is inline/absent; None if SAMPLE-AES."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line.upper().startswith("#EXT-X-KEY"):
            continue
        params = {}
        for part in line.split(":", 1)[-1].split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k.strip().upper()] = v.strip().strip('"')
        method = params.get("METHOD", "").upper()
        if method == "SAMPLE-AES":
            return None
        if method and method != "NONE" and params.get("URI"):
            return urljoin(base, params["URI"])
    return ""


def check(url: str) -> tuple[bool, str]:
    """Return (ok, note) after walking to and sniffing the first media segment."""
    ok, note = _check(url)
    if not ok and note.endswith(("Error", "Timeout")):
        ok, note = _check(url)  # one retry for transient network failures
    return ok, note


def _check(url: str) -> tuple[bool, str]:
    cur, duration, manifest, base = url, None, None, url
    try:
        for _ in range(3):
            parts = urlsplit(cur)
            if parts.scheme not in ("http", "https"):
                return False, "non-HTTP URL"
            with _fetch(cur) as r:
                if r.status_code not in (200, 206):
                    return False, f"HTTP {r.status_code}"
                ctype = r.headers.get("content-type", "")
                body = _read_body(r)
                if looks_like_html(ctype, body):
                    return False, "HTML page"
                if not body:
                    return False, "empty body"
                if not body.lstrip().startswith(b"#EXTM3U"):
                    if parts.path.lower().endswith((".m3u8", ".m3u")):
                        return False, "m3u8 is not a playlist"
                    kind = media_kind(body, ctype)
                    if kind is None:
                        return False, f"unknown payload ({ctype or 'no type'})"
                    return True, f"{kind} direct"
                text = body.decode("utf-8", "replace")
                manifest, base = text, r.url
                seg, duration = _first_segment(text)
                if not seg:
                    return False, "manifest without segments"
                key = _manifest_key_url(text, base)
                if key is None:
                    return False, "SAMPLE-AES encryption"
                cur = urljoin(r.url, seg)
                if "#EXT-X-STREAM-INF" in text:
                    continue  # master playlist, descend into the variant
                break
        else:
            return False, "too many playlist levels"

        parts = urlsplit(cur)
        if parts.scheme not in ("http", "https"):
            return False, "segment is not HTTP"
        key = _manifest_key_url(manifest or "", base)
        encrypted = bool(key)
        if encrypted:
            try:
                with requests.get(key, headers=headers_for(key),
                                  timeout=SEG_TIMEOUT) as kr:
                    if kr.status_code != 200:
                        return False, f"key HTTP {kr.status_code}"
                    if len(kr.content) != 16:
                        return False, "AES key not 16 bytes"
            except READ_EXC as exc:
                return False, f"key {type(exc).__name__}"

        deadline = max(2.0, duration * 0.8) if duration else SEG_DEADLINE
        start = time.monotonic()
        got = b""
        with _fetch(cur, SEG_TIMEOUT) as r:
            if r.status_code not in (200, 206):
                return False, f"segment HTTP {r.status_code}"
            ctype = r.headers.get("content-type", "")
            while len(got) < READ_LIMIT:
                left = deadline - (time.monotonic() - start)
                if left <= 0:
                    break
                chunk = r.raw.read(65536, decode_content=True)
                if not chunk:
                    break
                got += chunk
        elapsed = max(time.monotonic() - start, 1e-3)
        if len(got) < 32 * 1024:
            return False, f"segment {len(got)//1024}KB"
        if looks_like_html(ctype, got[:512]):
            return False, "segment is HTML"
        if got.lstrip().startswith(b"#EXTM3U"):
            return False, "segment is a playlist"
        kind = media_kind(got, ctype)
        if kind is None:
            if encrypted:
                return True, "AES-128 ok"
            return False, f"unknown payload ({ctype or 'no type'})"
        return True, f"{kind} ok"
    except READ_EXC as exc:
        return False, type(exc).__name__


def verify_file(path: str, out_path: str | None = None, workers: int = 24,
                report: str | None = None) -> tuple[int, int]:
    """Verify every entry in path; write kept entries to out_path (default in place)."""
    text = open(path, encoding="utf-8").read()
    header = [ln.rstrip("\n") for ln in text.splitlines()
              if ln.startswith("#") and not ln.startswith("#EXTINF")
              and ln.strip() != "#EXTM3U"]
    entries = parse_m3u(text)
    print(f"verify: {len(entries)} entries")
    bad: list[tuple[dict, str]] = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for e, (ok, note) in zip(entries, pool.map(check, [x["url"] for x in entries])):
            if not ok:
                bad.append((e, note))
    bad_urls = {b[0]["url"] for b in bad}
    good = [e for e in entries if e["url"] not in bad_urls]
    target = out_path or path
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n")
        for line in header:
            fh.write(line + "\n")
        for e in good:
            fh.write(e["extinf"] + "\n" + e["url"] + "\n")
    os.replace(tmp, target)
    print(f"verify: kept {len(good)}, dropped {len(bad)} in {time.time()-started:.0f}s -> {target}")
    for e, note in bad[:40]:
        print(f"  DROP {note:<28} {e['url'][:100]}")
    if report:
        with open(report, "w", encoding="utf-8") as fh:
            fh.write(f"verified: {len(entries)}, kept {len(good)}, dropped {len(bad)}\n\n")
            for e, note in bad:
                fh.write(f"{note}\t{e['url']}\n")
    return len(good), len(bad)


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else os.path.join(OUT, "merged.m3u")
    verify_file(path, report=os.path.join(os.path.dirname(path), "verify-report.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

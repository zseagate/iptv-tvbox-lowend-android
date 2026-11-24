#!/usr/bin/env python3
"""Build one merged IPTV playlist from several sources.

Sources (priority order, earlier wins when a channel name repeats):
  * zilong7728/Collect-IPTV best_sorted.m3u (via jsDelivr, refreshed upstream)
  * iptv-org playlists (news / cn / hk / tw / zh / movies / sports / entertainment)

Pipeline: fetch -> dedupe by URL -> probe every stream -> drop dead ->
dedupe by channel name -> write merged.m3u.

Environment:
  IPTV_OUT       output directory   (default: ../out)
  IPTV_CACHE     probe cache json   (default: $IPTV_OUT/probe-cache.json)

Outputs:
  merged.m3u        final playlist (what the STB points at)
  best_sorted.m3u   jsDelivr-only filtered list (kept for compatibility)
  report.txt        per-URL probe result
"""
from __future__ import annotations

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from filter_playlist import (CACHE_TTL, OUT, REFERER, SKIP_HOSTS, UA, WORKERS,  # noqa: F401
                             load_cache, looks_like_html, parse_m3u, probe,
                             reachable, save_cache)
from verify_playlist import verify_file

IPTVORG = "https://iptv-org.github.io/iptv/{}.m3u"
LIVE_URL = os.environ.get(
    "IPTV_LIVE_URL",
    "https://cdn.jsdelivr.net/gh/zilong7728/Collect-IPTV@main/best_sorted.m3u",
)

# channel-name blocklist (case-insensitive substring), env override: IPTV_SKIP_NAMES
SKIP_NAMES = tuple(
    n for n in os.environ.get("IPTV_SKIP_NAMES", "").replace(";", ",").split(",") if n
)

# priority order: earlier sources win when the same channel name appears twice
SOURCES = [
    ("jsdelivr-best", LIVE_URL),
    # iptv-org (public, stable) replaced zseagate/hypnotix after that repo was
    # deleted upstream. entertainment.m3u stands in for the old freetv list.
    ("iptv-org-news", IPTVORG.format("categories/news")),
    ("iptv-org-cn", IPTVORG.format("countries/cn")),
    ("iptv-org-hk", IPTVORG.format("countries/hk")),
    ("iptv-org-tw", IPTVORG.format("countries/tw")),
    ("iptv-org-zh", IPTVORG.format("languages/zho")),
    ("iptv-org-movies", IPTVORG.format("categories/movies")),
    ("iptv-org-sports", IPTVORG.format("categories/sports")),
    ("iptv-org-ent", IPTVORG.format("categories/entertainment")),
    # fnos-optimize 综合直播: BBC News (公网 akamaized 直连) + CNN International
    # (本地网关 192.168.10.100:18080 拉平, 同网段盒子可直连)。
    ("fnos-bbc", "https://vs-hls-push-ww-live.akamaized.net/x=4/i=urn:bbc:pips:service:bbc_news_channel_hd/t=3840/v=pv14/b=5070016/main.m3u8"),
    ("fnos-cnn", "http://192.168.10.100:18080/cnn.m3u8"),
]


def fetch(url: str, attempts: int = 3, backoff: float = 2.0) -> str:
    """Fetch a source playlist, retrying transient network errors.

    A single flaky fetch (SSLError / ReadTimeout / ConnectionError) used to
    drop the whole source, silently removing its channels (e.g. Al Jazeera
    English came from a news source and vanished when that fetch glitched).
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=(8, 30))
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except requests.RequestException as exc:
            last = exc
            print(f"  fetch retry {i + 1}/{attempts} {type(exc).__name__}: {url}")
            if i < attempts - 1:
                time.sleep(backoff * (i + 1))
    raise last if last else RuntimeError(f"fetch failed: {url}")


def norm_name(entry: dict) -> str:
    name = (entry.get("name") or "").strip().lower()
    name = re.sub(r"\s+", "", name).replace("－", "-")
    # providers differ on separators: CCTV-13 / CCTV13 / CCTV 13
    return re.sub(r"^cctv[-_ ]?(\d+\+?)$", r"cctv\1", name)


def channel_name(extinf: str) -> str:
    return extinf.rsplit(",", 1)[-1].strip() if "," in extinf else extinf


# ---- Re-classification: unify mixed-source groups into Chinese categories ----
# jsDelivr (Collect-IPTV) groups are already Chinese and kept verbatim.
# iptv-org groups are English category names and mapped below.
_GRP_RULES = [
    (r"news|新聞|新闻|時事", "新闻频道"),
    (r"movie|movies|film|cinema|series|classic|comedy|family", "电影频道"),
    (r"sport|体育", "体育频道"),
    (r"document|纪录|紀錄|discovery|culture|cultural|人文", "纪录人文"),
    (r"entertainment|综艺|綜藝|variety", "综艺频道"),
    (r"lifestyle|生活|life|food|travel|天气|天气", "生活频道"),
    (r"kid|child|少儿|兒童|cartoon|animation|动漫|動漫", "少儿动漫"),
    (r"music|音乐|音樂", "音乐频道"),
    (r"shop|购物|購物|mall", "购物频道"),
    (r"religious|宗教|faith|church", "其他频道"),
    (r"education|教育|learning", "教育频道"),
]

# region names used by the freetv source
_REGIONS = ("italy", "greece", "russia", "uk", "spain", "france", "germany",
            "usa", "turkey", "bulgaria", "portugal", "romania", "poland",
            "sweden", "norway", "denmark", "ukraine", "netherlands", "serbia")


def classify_group(e: dict) -> str:
    """Return a canonical Chinese group-title for a merged entry."""
    name = channel_name(e["extinf"])

    # All CCTV / CGTN / 央视 channels belong to 央视频道, regardless of source
    # or the group the upstream list put them in (news/education/music/...).
    if re.search(r"\bcctv|cgtn|央视|中央电视|中央台", name, re.I):
        return "央视频道"

    # HK/Taiwan broadcasters: some sources group them under a mainland
    # province (e.g. 凤凰中文 under 湖南频道) — keep them in 港澳台频道.
    if re.search(r"凤凰(卫视|衛視|中文|资讯|資訊|香港)"
                 r"|鳳凰(衛視|中文|資訊|香港)"
                 r"|phoenix(chinese|infonews)"
                 r"|\btvb|翡翠台|明珠台|東森|东森|中天(新闻|新聞|電視|电视|综合|綜合)"
                 r"|三立|中视|中視|华视|華視|台视|台視|民视|民視|纬来|緯來|\btvbs"
                 r"|澳视|澳視", name, re.I):
        return "港澳台频道"

    # Movie streams sometimes sit in a province group upstream (湖南频道 has
    # a batch of 电影 channels) — always file them under 电影频道.
    # 特种兵之火凤凰 is a movie/series stream that upstream keeps in 湖南频道.
    if any(h in name for h in ("电影", "電影", "特种兵之火凤凰")):
        return "电影频道"

    if e["source"] == "jsdelivr-best":
        g = re.search(r'group-title="([^"]*)"', e["extinf"])
        if g and g.group(1):
            return g.group(1)
        return "其他频道"

    g = re.search(r'group-title="([^"]*)"', e["extinf"])
    grp = (g.group(1) if g else "").strip()

    # iptv-org entertainment = international general channels (was freetv)
    if e["source"] == "iptv-org-ent":
        if not grp:
            return "海外频道"
        low = grp.lower()
        for rg in _REGIONS:
            if rg in low:
                return f"海外频道·{rg.capitalize()}"
        return "海外频道"

    # keyword rules on the group-title (or channel name as backup)
    hay = grp + " " + channel_name(e["extinf"])
    # fnos 综合直播: BBC/CNN 都是新闻频道
    if e["source"] in ("fnos-bbc", "fnos-cnn"):
        return "新闻频道"
    for pat, cat in _GRP_RULES:
        if re.search(pat, hay, re.I):
            return cat

    # generic english group titles that tell us nothing -> guess from name
    if grp.lower() in ("undefined", "general", "") or grp.startswith("新聞"):
        name = channel_name(e["extinf"])
        if re.search(r"cctv|央视|中央", name, re.I):
            return "央视频道"
        if re.search(r"卫视|衛視|tvb|鳳凰|凤凰|东森|中天|三立|hktv", name):
            return "卫视频道" if re.search(r"卫视|衛視", name) else "港澳台频道"
        if re.search(r"^[\u4e00-\u9fff]*[省市][^ ]*$", name) and len(name) <= 12:
            return "地方频道"
        return "其他频道"

    # already Chinese group -> keep
    if re.search(r"[\u4e00-\u9fff]", grp):
        return grp
    return "其他频道"


def reclassify(entries: list[dict]) -> None:
    """Rewrite group-title in-place for each entry (mutates extinf)."""
    for e in entries:
        e["extinf"] = re.sub(
            r'group-title="[^"]*"',
            f'group-title="{classify_group(e)}"',
            e["extinf"],
        )


# ---- Custom display order for merged.m3u ----
# 1) CCTV channels first (group 央视频道), numbered CCTV-1/2/3/... order;
# 2) remaining groups in the user's preferred order; 3) groups stay contiguous;
# 4) within a group, channels are sorted by English alphabetical order.
_GROUP_SORT_ORDER = [
    "电影频道", "少儿动漫", "新闻频道", "音乐频道",
    "港澳台频道", "综艺频道", "人文频道", "体育频道", "教育频道",
    "文旅频道", "生活频道", "其他频道", "海外频道",
    "卫视频道", "各省频道",
]
# keep the original labels but place them at the canonical slot
_GROUP_SORT_ALIASES = {
    "纪录人文": "人文频道",
    "地方频道": "各省频道",
}
# groups that look like "XX频道" but are categories, not provinces
_KNOWN_GROUPS = set(_GROUP_SORT_ORDER) | {
    "央视频道", "纪录人文", "综合", "购物频道", "少儿动漫",
}
_PROVINCE_GROUP_RE = re.compile(r"^[\u4e00-\u9fff]{2,4}频道$")
_CCTV_NUM_RE = re.compile(r"cctv[-_ ]?(\d+)", re.I)


def _canonical_group(grp: str) -> str:
    """Map a group-title to the slot used for ordering (labels unchanged)."""
    base = grp.split("·")[0].strip() or "其他频道"
    if base in _GROUP_SORT_ALIASES:
        return _GROUP_SORT_ALIASES[base]
    if base in _KNOWN_GROUPS:
        return base
    # 浙江频道 / 湖南频道 / ... -> 各省频道 slot
    if _PROVINCE_GROUP_RE.match(base):
        return "各省频道"
    return base


def sort_key(e: dict) -> tuple:
    m = re.search(r'group-title="([^"]*)"', e.get("extinf") or "")
    grp = (m.group(1).strip() if m and m.group(1) else "") or "其他频道"
    name = (e.get("name") or channel_name(e["extinf"] or "")).strip().lower()
    if grp == "央视频道" or re.search(r"\bcctv|cgtn|央视|中央电视|中央台", name, re.I):
        # numbered CCTV-1/2/.../17 first (numeric, not lexical), then the
        # other CCTV/CCTV+/CGTN channels alphabetically.
        num = _CCTV_NUM_RE.search(name)
        if num:
            return (0, 0, int(num.group(1)), "", "", name)
        return (0, 1, 9999, "", "", name)
    canon = _canonical_group(grp)
    rank = (_GROUP_SORT_ORDER.index(canon) if canon in _GROUP_SORT_ORDER
            else len(_GROUP_SORT_ORDER))
    # (rank, canonical slot, exact group, channel): exact group keeps
    # 海外频道·Italy / 浙江频道 etc. contiguous, then alphabetical by name.
    return (1, rank, 0, canon, grp, name)


def sort_final(entries: list[dict]) -> list[dict]:
    """CCTV numeric first, then user-ordered groups (contiguous), alphabetical inside."""
    return sorted(entries, key=sort_key)



def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    cache = load_cache()
    now = time.time()

    tagged: list[dict] = []
    stats = {}

    # Keep the previous playlist as the highest-priority baseline source so a
    # weaker/partial upstream (or a deleted repo) can't drop channels that
    # already work. Fresh sources below only add new channels; the probe step
    # still re-checks reachability once a cached result passes its TTL.
    baseline_path = os.path.join(OUT, "merged.m3u")
    if os.path.exists(baseline_path):
        with open(baseline_path, encoding="utf-8") as fh:
            base_entries = parse_m3u(fh.read())
        for e in base_entries:
            e["name"] = channel_name(e["extinf"])
            e["source"] = "baseline"
        tagged.extend(base_entries)
        stats["baseline"] = len(base_entries)
        print(f"[ok] baseline: {len(base_entries)} channels (previous playlist)")

    for label, url in SOURCES:
        try:
            text = fetch(url)
        except Exception as exc:
            print(f"[skip] {label}: {type(exc).__name__}")
            stats[label] = 0
            continue
        # fnos 综合直播: URL 是单频道媒体播单 (分片列表), 不是频道列表。
        # 不要用 parse_m3u 解析分片, 直接把整条 URL 作为一个频道。
        if label == "fnos-bbc":
            entries = [{"extinf": '#EXTINF:-1 group-title="综合",BBC News', "url": url}]
        elif label == "fnos-cnn":
            entries = [{"extinf": '#EXTINF:-1 group-title="综合",CNN International', "url": url}]
        else:
            entries = parse_m3u(text)
        stats[label] = len(entries)
        for e in entries:
            e["name"] = channel_name(e["extinf"])
            e["source"] = label
            tagged.append(e)
        print(f"[ok] {label}: {len(entries)} channels")

    if not tagged:
        # never clobber a good playlist with an empty one (e.g. no DNS right
        # after boot); keep whatever is on disk and report failure
        print("[abort] all sources failed to fetch; keeping previous playlist")
        return 2

    seen_url, unique = set(), []
    for e in tagged:
        if e["url"] not in seen_url:
            seen_url.add(e["url"])
            unique.append(e)
    print(f"total={len(tagged)} unique-url={len(unique)} dup-url={len(tagged)-len(unique)}")

    results, todo = {}, []
    for e in unique:
        u = e["url"]
        c = cache.get(u)
        if any(h in u for h in SKIP_HOSTS):
            results[u] = {"alive": False, "note": "YouTube link (unsupported by TiviMate)", "ts": now}
        elif c and now - c.get("ts", 0) < CACHE_TTL:
            results[u] = c
        else:
            todo.append(u)

    print(f"cached={len(results)} probing={len(todo)}")

    # Pass 1: cheap reachability pre-filter (1KB fetch) so the expensive media
    # probe below only touches hosts that answer. Without this, probing 1000s
    # of streams at once saturates the uplink and misjudges healthy channels.
    reachable_ok, to_probe = {}, []
    started, done = time.time(), 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for u, (ok, note) in zip(todo, pool.map(reachable, todo)):
            if ok:
                to_probe.append(u)
            else:
                reachable_ok[u] = {"alive": False, "note": note, "ts": time.time()}
            done += 1
            if done % 200 == 0:
                print(f"  reachability {done}/{len(todo)} ({time.time()-started:.0f}s)")
    print(f"reachability: ok={len(to_probe)} dead={len(reachable_ok)}")
    results.update(reachable_ok)

    # Pass 2: strict media probe on the reachable subset.
    started, done = time.time(), 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for u, (alive, note) in zip(to_probe, pool.map(probe, to_probe)):
            results[u] = {"alive": alive, "note": note, "ts": time.time()}
            done += 1
            if done % 200 == 0:
                print(f"  probed {done}/{len(to_probe)} ({time.time()-started:.0f}s)")

    cache.update(results)
    save_cache(cache)

    alive_by_url = [e for e in unique if results.get(e["url"], {}).get("alive")]

    # one entry per channel name (first source in priority order wins)
    final, taken = [], set()
    for e in alive_by_url:
        key = norm_name(e) or e["url"]
        if key in taken:
            continue
        taken.add(key)
        final.append(e)

    gen = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S %Z")
    # Channel-name blocklist (e.g. dead/looping mirrors such as TVBS).
    if SKIP_NAMES:
        before = len(final)
        final = [e for e in final
                 if not any(n.lower() in (e.get("name") or channel_name(e["extinf"])).lower()
                            for n in SKIP_NAMES)]
        print(f"name-blocklist removed {before - len(final)} channels")
    # Re-classify groups into unified Chinese categories before writing.
    reclassify(final)

    # Custom display order: CCTV first, then user-ordered groups, then
    # alphabetical within group.
    final = sort_final(final)

    # Guard against source-outage clobbering: if a big chunk of the previous
    # playlist suddenly vanished (e.g. an upstream repo got deleted and the
    # fetch silently dropped its channels), keep the old good playlist instead
    # of publishing a gutted one.
    merged_path = os.path.join(OUT, "merged.m3u")
    prev_count = 0
    if os.path.exists(merged_path):
        with open(merged_path, encoding="utf-8") as fh:
            prev_count = sum(1 for line in fh if line.startswith("#EXTINF"))
    if prev_count and len(final) < prev_count * 0.7:
        print(f"[abort] channels dropped {prev_count} -> {len(final)} "
              f"(<70% of previous); keeping previous playlist")
        return 3

    # UA hint that players honor (TiviMate reads #EXTVLCOPT:http-user-agent).
    # Probing uses IPTV_UA, but many sources only accept the VLC UA on playback.
    playlist_ua = os.environ.get("IPTV_PLAYLIST_UA", "VLC/3.0.20 LibVLC/3.0.20")
    header = [
        f"# Filtered-Time: {gen}",
        "# Sources: baseline, " + ", ".join(l for l, _ in SOURCES),
        "# Filter: dedupe by URL + channel name, every stream probed, dead removed",
        "# Classify: groups unified into Chinese categories (央视/卫视/新闻/电影/体育/海外…)",
        f"# Probe: UA={UA} Referer={REFERER or '-'}",
        f"# Channel-Count: {len(final)}",
        f"#EXTVLCOPT:http-user-agent={playlist_ua}",
    ]

    def write(path: str, entries: list[dict]) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("#EXTM3U\n")
            for line in header:
                fh.write(line + "\n")
            for e in entries:
                fh.write(e["extinf"] + "\n" + e["url"] + "\n")
        os.replace(tmp, path)

    write(merged_path, final)

    # second pass: drop entries ExoPlayer would fail to parse (ParserException)
    try:
        kept, dropped = verify_file(merged_path,
                                    report=os.path.join(OUT, "verify-report.txt"))
        if dropped:
            source_by_url = {e["url"]: e["source"] for e in final}
            final = parse_m3u(open(merged_path, encoding="utf-8").read())
            for e in final:
                e["source"] = source_by_url.get(e["url"], "unknown")
            for i, line in enumerate(header):
                if line.startswith("# Channel-Count:"):
                    header[i] = f"# Channel-Count: {len(final)}"
            header.append(f"# Verify: {dropped} unparseable entries removed")
            write(merged_path, final)
    except Exception as exc:  # verification is best-effort, never lose the playlist
        print(f"[warn] verify skipped: {type(exc).__name__}: {exc}")

    # best_sorted.m3u = the jsDelivr-sourced channels (kept for compatibility).
    # Match by URL, not source: with the baseline-first merge those entries
    # inherit source="baseline", so a source check would wrongly yield empty.
    jsdelivr_urls = {e["url"] for e in tagged if e["source"] == "jsdelivr-best"}
    write(os.path.join(OUT, "best_sorted.m3u"),
          [e for e in final if e["url"] in jsdelivr_urls])

    per_source = {}
    for e in final:
        per_source[e["source"]] = per_source.get(e["source"], 0) + 1

    with open(os.path.join(OUT, "report.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"generated: {gen}\n")
        fh.write(f"channels: {len(final)} (source entries {len(tagged)}, unique urls {len(unique)}, "
                 f"alive urls {len(alive_by_url)})\n")
        for label, _ in SOURCES:
            fh.write(f"  {label}: fetched {stats.get(label, 0)}, alive-kept {per_source.get(label, 0)}\n")
        fh.write("\nGROUPS\n")
        groups: dict[str, int] = {}
        for e in final:
            g = re.search(r'group-title="([^"]*)"', e["extinf"])
            groups[g.group(1) if g else "其他频道"] = groups.get(g.group(1) if g else "其他频道", 0) + 1
        for g, c in sorted(groups.items(), key=lambda x: -x[1]):
            fh.write(f"  {c:5d}  {g}\n")
        fh.write("\nSTATUS\tSOURCE\tURL\tNOTE\n")
        for e in unique:
            r = results.get(e["url"], {})
            fh.write(("OK" if r.get("alive") else "DEAD") + "\t" + e["source"] + "\t" +
                     e["url"] + "\t" + str(r.get("note", "")) + "\n")

    print(f"final channels={len(final)}")
    for label, _ in SOURCES:
        print(f"  {label}: {per_source.get(label, 0)}")
    print(f"written: {OUT}/merged.m3u")
    return 0


if __name__ == "__main__":
    sys.exit(main())

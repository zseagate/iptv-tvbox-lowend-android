"""仓库分组/排列规则（从 iptv-tivimate merge_playlists.py 提取，自包含）。

规则：
  classify_group(e):  把混合源分组重分类为统一中文分类（央视/电影/新闻/体育/港澳台/海外…）
  reclassify(entries): 就地改写每条 group-title
  sort_final(entries): 央视数字序优先 -> 分组顺序 -> 组内字母序
调用方需给每条 entry 提供 source 标签（jsdelivr-best / iptv-org-ent / fnos-bbc / fnos-cnn / 其它），
以及 name（频道名）与 extinf（含原 group-title 的 #EXTINF 行）。
"""
from __future__ import annotations
import re


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

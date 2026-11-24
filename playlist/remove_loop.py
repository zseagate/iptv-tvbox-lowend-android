#!/usr/bin/env python3
"""Remove looping/recorded channels (verified by content hash) from a playlist.

Matches by channel name (unique in the playlist).  Removes the #EXTINF
line and the following URL line.

Usage: python3 remove_loop.py <input.m3u> <content-check.tsv> <output.m3u>
"""
import sys


def main() -> int:
    src, check_tsv, dst = sys.argv[1], sys.argv[2], sys.argv[3]

    # Collect loop-confirm URLs -> their channel names from the source playlist.
    loop_urls = set()
    with open(check_tsv, encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            u, v, d = line.rstrip("\n").split("\t")
            if v == "loop-confirm":
                loop_urls.add(u)

    # Map URL -> channel name in source.
    url2name = {}
    with open(src, encoding="utf-8") as fh:
        cur = None
        for raw in fh:
            l = raw.strip()
            if l.startswith("#EXTINF"):
                cur = l.rsplit(",", 1)[-1].strip() if "," in l else l
            elif not l.startswith("#") and l and cur:
                url2name[l] = cur
                cur = None

    drop_names = {url2name[u] for u in loop_urls if u in url2name}
    print(f"loop-confirm URLs: {len(loop_urls)}, mapped names: {len(drop_names)}")

    removed = 0
    kept = 0
    with open(src, encoding="utf-8") as fin, open(dst, "w", encoding="utf-8") as fout:
        for raw in fin:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if stripped.startswith("#EXTINF"):
                name = stripped.rsplit(",", 1)[-1].strip() if "," in stripped else stripped
                # peek next line
                nxt = next(fin, "")
                if name in drop_names:
                    removed += 1
                    continue  # drop EXTINF and URL
                fout.write(line + "\n")
                if nxt:
                    fout.write(nxt.rstrip("\n") + "\n")
                kept += 1
            elif stripped == "":
                fout.write(line + "\n")
    print(f"kept={kept} removed={removed}")
    print(f"written: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

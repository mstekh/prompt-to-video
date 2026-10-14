"""Пошук і завантаження легальних зображень з Wikimedia Commons.

Використання:
  py -3.14 commons_fetch.py --query "Lydian electrum stater" --out cand/S6 --n 5
  py -3.14 commons_fetch.py --query "..." --dry        # лише показати кандидатів, нічого не качати

Приймаються лише ліцензії: суспільне надбання / CC0 / CC BY (CC BY-SA позначається sa=true).
Кожне завантаження дописується в <out>/meta.json (автор, ліцензія, сторінка файлу).
"""
import argparse, html, json, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

API = "https://commons.wikimedia.org/w/api.php"
UA = "AIVideoLabTest/1.0 (personal quick test; python-urllib)"
MIN_H, MIN_W = 1300, 900


def get(url, binary=False, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            return data if binary else json.loads(data.decode("utf-8"))
        except Exception as e:  # 429 / мережа
            time.sleep(1.5 * (i + 1))
            err = e
    raise RuntimeError(f"запит не вдався: {err}")


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def license_ok(short):
    s = (short or "").lower()
    if any(x in s for x in ("nc", "nd", "non-commercial", "fair use")):
        return False, False
    if s.startswith(("public domain", "pd", "cc0", "cc by", "cc-by", "no restrictions")) or "public domain" in s:
        return True, "sa" in s
    return False, False


def search(query, limit=30, min_h=MIN_H):
    p = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
         "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": limit, "prop": "imageinfo",
         "iiprop": "url|size|mime|extmetadata", "iiurlheight": 2400}
    d = get(API + "?" + urllib.parse.urlencode(p))
    out = []
    for pg in (d.get("query", {}).get("pages", {}) or {}).values():
        ii = (pg.get("imageinfo") or [{}])[0]
        if ii.get("mime") not in ("image/jpeg", "image/png"):
            continue
        w, h = ii.get("width", 0), ii.get("height", 0)
        if h < min_h or w < MIN_W:
            continue
        md = ii.get("extmetadata", {})
        lic = (md.get("LicenseShortName") or {}).get("value", "")
        ok, sa = license_ok(lic)
        if not ok:
            continue
        out.append({
            "pageid": pg["pageid"], "title": pg["title"], "license": lic, "sa": sa,
            "author": clean((md.get("Artist") or {}).get("value", ""))[:120],
            "descr": clean((md.get("ImageDescription") or {}).get("value", ""))[:160],
            "page": ii.get("descriptionurl"), "w": w, "h": h,
            "thumb": ii.get("thumburl") or ii.get("url"),
        })
    out.sort(key=lambda c: (c["sa"], -min(c["h"], 2400)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--min-h", type=int, default=MIN_H)
    a = ap.parse_args()
    cands = search(a.query, min_h=a.min_h)[: a.n]
    if a.dry or not a.out:
        print(json.dumps(cands, ensure_ascii=False, indent=1))
        return
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    mp = out / "meta.json"
    meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else []
    have = {m["pageid"] for m in meta}
    new = 0
    for c in cands:
        if c["pageid"] in have:
            continue
        fn = out / f"{c['pageid']}.jpg"
        try:
            fn.write_bytes(get(c["thumb"], binary=True))
        except Exception as e:
            print("пропущено", c["title"], e)
            continue
        c["file"] = str(fn)
        c["query"] = a.query
        meta.append(c)
        new += 1
        time.sleep(0.6)
    mp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{a.query!r}: знайдено {len(cands)}, нових {new}, усього в {out.name}: {len(meta)}")


if __name__ == "__main__":
    main()

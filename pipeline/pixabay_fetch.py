"""Відео з Pixabay API (ліцензія Pixabay: комерційно можна, атрибуція не обов'язкова). Ключ — змінна PIXABAY_API_KEY.

  py -3.14 pixabay_fetch.py --query "gold bars" --out src/S2 --n 3
Бере файл medium/large (до 1920 px), тривалість 4–60 с. Дописує <out>/meta.json (сумісно з cut_segments.py).
"""
import argparse, json, os, sys, time, urllib.parse, urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
KEY = os.environ.get("PIXABAY_API_KEY")
if not KEY:
    sys.exit("PIXABAY_API_KEY не задано")


def get(url, binary=False):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AIVideoLab/1.0"}), timeout=120) as r:
                d = r.read()
            return d if binary else json.loads(d.decode("utf-8"))
        except Exception as e:
            err = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(err)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=3)
    a = ap.parse_args()
    q = urllib.parse.urlencode({"key": KEY, "q": a.query, "per_page": 20, "safesearch": "true", "order": "popular"})
    hits = get("https://pixabay.com/api/videos/?" + q).get("hits", [])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    mp = out / "meta.json"
    meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else []
    have = {m["id"] for m in meta}
    new = 0
    for h in hits:
        if new >= a.n:
            break
        vid = f"px{h['id']}"
        if vid in have or not (4 <= h.get("duration", 0) <= 60):
            continue
        v = h.get("videos", {})
        f = v.get("large") if v.get("large", {}).get("width", 0) and v["large"]["width"] <= 1920 else v.get("medium")
        if not f or not f.get("url"):
            continue
        fn = out / f"{vid}.mp4"
        try:
            fn.write_bytes(get(f["url"], binary=True))
        except Exception as e:
            print("пропущено", vid, e)
            continue
        meta.append({"id": vid, "file": str(fn), "title": h.get("tags", ""), "license": "Pixabay Content License",
                     "url": h.get("pageURL"), "author": h.get("user"), "duration": h.get("duration"),
                     "query": a.query, "source": "pixabay"})
        new += 1
    mp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{a.query!r}: знайдено {len(hits)}, завантажено {new}, усього в {out.name}: {len(meta)}")


if __name__ == "__main__":
    main()

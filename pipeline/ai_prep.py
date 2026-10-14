"""Для кожного рядка plan.json: знайти на Commons зображення (PD/CC0/CC BY), зробити стартовий кадр 768x1344,
скласти контактний аркуш. Огляд кадрів — моделлю на Kaggle (окремо), Claude картинок не дивиться."""
import json, subprocess, sys
from pathlib import Path
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw

sys.stdout.reconfigure(encoding="utf-8")
D = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()   # тека з plan.json
ROOT = Path(__file__).resolve().parent  # тут лежить commons_fetch.py
W, H = 768, 1344
plan = json.loads((D / "plan.json").read_text(encoding="utf-8"))


def start_frame(src, dst):
    im = Image.open(src).convert("RGB")
    ar = im.width / im.height
    s = max(W / im.width, H / im.height)
    big = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    l, t = (big.width - W) // 2, (big.height - H) // 2
    if ar < 0.9:
        out = big.crop((l, t, l + W, t + H))
    else:
        out = ImageEnhance.Brightness(big.crop((l, t, l + W, t + H)).filter(ImageFilter.GaussianBlur(25))).enhance(0.45)
        cw = W - 40
        ch = round(cw / ar)
        out.paste(im.resize((cw, ch), Image.LANCZOS), (20, (H - ch) // 2))
    out.save(dst)


done = []
for p in plan:
    out = D / f"{p['id']}_start.png"
    if out.exists():
        done.append(p); continue
    srcdir = D / f"src_{p['id']}"
    subprocess.run([sys.executable, str(ROOT / "commons_fetch.py"), "--query", p["q"], "--out", str(srcdir), "--n", "1",
                    "--min-h", "600"], capture_output=True, text=True, env={**__import__("os").environ, "PYTHONUTF8": "1"})
    meta = srcdir / "meta.json"
    if not meta.exists() or not json.loads(meta.read_text(encoding="utf-8")):
        print(p["id"], "НЕ ЗНАЙДЕНО:", p["q"]); continue
    m = json.loads(meta.read_text(encoding="utf-8"))[0]
    start_frame(m["file"], out)
    p["source"] = {k: m[k] for k in ("title", "license", "author", "page")}
    done.append(p)
    print(p["id"], "ок:", m["title"][5:70], "|", m["license"])
(D / "plan_resolved.json").write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
# контактний аркуш для огляду моделлю
th = []
for p in done:
    im = Image.open(D / f"{p['id']}_start.png"); im.thumbnail((192, 336)); th.append((p["id"], im))
cols = 6
sheet = Image.new("RGB", (cols * 196, ((len(th) + cols - 1) // cols) * 360), "white")
dr = ImageDraw.Draw(sheet)
for i, (pid, im) in enumerate(th):
    x, y = (i % cols) * 196, (i // cols) * 360
    sheet.paste(im, (x + 2, y + 20)); dr.text((x + 4, y + 2), pid, fill="black")
sheet.save(D / "start_frames_sheet.jpg", quality=80)
print("готово:", len(done), "із", len(plan))

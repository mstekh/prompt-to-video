"""Генерує storyboard.html (розкадровка ролика) з shots.json, clips.json, segments.json, reviews.json і qa.json."""
import base64, html, io, json, sys
from pathlib import Path
from PIL import Image

W = Path(__file__).parent
cfg = json.loads((W / "shots.json").read_text(encoding="utf-8"))
clips = json.loads((W / "clips.json").read_text(encoding="utf-8"))
segs = json.loads((W / "work/segments.json").read_text(encoding="utf-8"))
revs = json.loads((W / "work/review_out/reviews.json").read_text(encoding="utf-8"))
qa = json.loads((W / "work/render_out/qa.json").read_text(encoding="utf-8"))
by_key = {(s["file"], round(s["start"], 2)): s for s in segs}
TOTAL = sum(s["dur"] for s in cfg["shots"])
e = html.escape


def thumb(rel):
    im = Image.open(W / "work/thumbs" / rel).convert("RGB")
    im.thumbnail((220, 220))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=72)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def tc(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


bars, rows = [], []
for i, s in enumerate(cfg["shots"]):
    pct = s["dur"] / TOTAL * 100
    bars.append(f'<a class="seg" href="#{s["id"]}" style="flex-basis:{pct:.3f}%" title="{e(s["label"])}"><span>{s["id"][1:]}</span></a>')
    frames = []
    for c in clips.get(s["id"], []):
        sg = by_key.get((c["file"], round(c["start"], 2)))
        if not sg:
            continue
        rv = revs.get(sg["seg"], {})
        src = c.get("url") or ""
        frames.append(
            f'<figure class="frame"><img src="{thumb(sg["thumbs"][1])}" alt="{e(str(rv.get("desc", "")))}" loading="lazy">'
            f'<figcaption><span class="desc">{e(str(rv.get("desc", ""))[:70])}</span>'
            f'<span class="meta">оцінка {rv.get("match", "–")}/{rv.get("quality", "–")} · '
            f'<a href="{e(src)}" target="_blank" rel="noopener">Pixabay</a></span></figcaption></figure>')
    title_note = '<p class="note">Заставка: напис поверх затемненого кліпу</p>' if s["kind"] == "title" else ""
    rows.append(f'''
<section class="shot" id="{s["id"]}">
  <header class="shot-head">
    <span class="num">{s["id"][1:]}</span>
    <span class="time">{tc(s["start"])} – {tc(s["start"] + s["dur"])}</span>
    <span class="dur">{s["dur"]:.1f} с</span>
  </header>
  <div class="shot-body">
    <div class="copy">
      <p class="label">{e(s["label"])}</p>
      <p class="vo">{e(s["text"])}</p>
      {title_note}
    </div>
    <div class="strip">{"".join(frames) or '<p class="note">кліпів немає</p>'}</div>
  </div>
</section>''')

problems = [p for p in qa.get("problems", []) if "тривалість" not in p]
page = (W / "storyboard_template.html").read_text(encoding="utf-8")
page = (page.replace("{{BARS}}", "".join(bars)).replace("{{ROWS}}", "".join(rows))
        .replace("{{TOTAL}}", f"{TOTAL:.0f}").replace("{{LUFS}}", str(qa.get("loudness_lufs")))
        .replace("{{PEAK}}", str(qa.get("true_peak_dbfs")))
        .replace("{{NCLIPS}}", str(sum(len(v) for v in clips.values())))
        .replace("{{NSEGS}}", str(len(revs)))
        .replace("{{PROBLEMS}}", e("; ".join(problems)) or "немає"))
(W / "storyboard.html").write_text(page, encoding="utf-8")
print("storyboard.html", round(len(page) / 1024), "KB")

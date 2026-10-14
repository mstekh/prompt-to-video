"""Черга AI-кліпів: для кожного рядка plan_resolved.json запускає clip_runner.py (по одному запуску Kaggle на кліп).

  py clip_queue.py --plan work/ai_clips/plan_resolved.json --frames work/ai_clips --out work/ai_clips [--only S1a,S2b]
Пропускає кліпи, які вже є в --out. Безпечно перезапускати: продовжує з того місця, де зупинилось.
Запускати окремо від терміналу, якщо сесія може перерватися (Windows: Win32_Process.Create; Linux: nohup/systemd-run).
"""
import argparse, json, subprocess, sys, time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--plan", required=True)
ap.add_argument("--frames", required=True, help="тека зі стартовими кадрами <id>_start.png")
ap.add_argument("--out", required=True)
ap.add_argument("--only", default="")
a = ap.parse_args()
plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
only = {x for x in a.only.split(",") if x}
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
for p in plan:
    if only and p["id"] not in only:
        continue
    dst = out / f"{p['id'].lower()}.mp4"
    if dst.exists():
        print(p["id"], "уже є"); continue
    t0 = time.time()
    r = subprocess.run([sys.executable, str(Path(__file__).with_name("clip_runner.py")), "--image",
                        str(Path(a.frames) / f"{p['id']}_start.png"), "--prompt", p["prompt"],
                        "--name", p["id"].lower(), "--out", str(out)])
    print(p["id"], "ок" if r.returncode == 0 else f"ПОМИЛКА {r.returncode}", f"{(time.time() - t0) / 60:.0f} хв", flush=True)

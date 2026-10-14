"""Збірка ролика з готових AI-кліпів: clips.json -> пакет -> рендер на Kaggle -> 720p-чернетка -> (необов'язково) сповіщення.

  py assemble.py --shots shots.json --clips-dir work/ai_clips --plan work/ai_clips/plan_resolved.json --groups groups.json
groups.json: {"S1": ["s1c","s1a"], "S2": ["s2a","s2c"], ...} — які кліпи (імена файлів без .mp4) йдуть у який кадр сценарію.
Сповіщення: змінна NOTIFY_CMD, напр.  NOTIFY_CMD='python notify.py'  — їй передається шлях до відео та підпис.
"""
import argparse, json, os, shutil, subprocess, sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--clips-dir", required=True)
ap.add_argument("--plan", required=True)
ap.add_argument("--groups", required=True)
ap.add_argument("--out", default="work/render_out")
a = ap.parse_args()
H = Path(__file__).resolve().parent
ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
plan = {p["id"].lower(): p for p in json.loads(Path(a.plan).read_text(encoding="utf-8"))}
groups = json.loads(Path(a.groups).read_text(encoding="utf-8"))

clips, missing = {}, []
for shot, names in groups.items():
    lst = []
    for n in names:
        f = Path(a.clips_dir) / f"{n}.mp4"
        if not f.exists():
            missing.append(n); continue
        src = plan.get(n, {}).get("source", {})
        lst.append({"file": str(f), "start": 0.0, "dur": 5.0,
                    "title": f"AI-кліп (MiniMax H3) зі стартового кадру: {src.get('title', '')}",
                    "license": src.get("license", ""), "url": src.get("page", "")})
    clips[shot] = lst
print("кліпів по кадрах:", {k: len(v) for k, v in clips.items()}, "бракує:", missing)
Path("clips.json").write_text(json.dumps(clips, ensure_ascii=False, indent=1), encoding="utf-8")


def run(args):
    print("$", " ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), env=ENV).returncode


out = Path(a.out)
if run([sys.executable, H / "make_render_bundle.py"]) or run([sys.executable, H / "kaggle_job.py", "--name", "pipeline-render",
        "--script", H / "kernel_render.py", "--data", "work/render_bundle", "--timeout-min", "90", "--out", out]):
    if not (out / "final.mp4").exists():
        sys.exit("рендер на Kaggle не вдався")
import imageio_ffmpeg
draft = out / "draft_720p.mp4"
run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-threads", "2", "-i", out / "final.mp4",
     "-vf", "scale=720:1280:flags=lanczos", "-c:v", "libx264", "-preset", "medium", "-crf", "26", "-pix_fmt", "yuv420p",
     "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", draft])
qa = json.loads((out / "qa.json").read_text(encoding="utf-8")) if (out / "qa.json").exists() else {}
caption = f"Чернетка 720p. QA: {qa.get('loudness_lufs')} LUFS; зауваження: {'; '.join(qa.get('problems', [])) or 'немає'}"
print(caption)
if os.environ.get("NOTIFY_CMD"):
    subprocess.run([*os.environ["NOTIFY_CMD"].split(), str(draft), caption], env=ENV)

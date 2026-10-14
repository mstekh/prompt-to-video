"""Автоперевірка готового ролика: жорсткі вимірювання + огляд кадрів локальною gemma3:4b.

  py -3.14 qa.py out/money_60s.mp4 [--no-vision]
"""
import base64, io, json, re, subprocess, sys
from pathlib import Path
import urllib.request
import imageio_ffmpeg
from PIL import Image

W = Path(__file__).parent
FF = imageio_ffmpeg.get_ffmpeg_exe()
video = Path(sys.argv[1])
cfg = json.loads((W / "shots.json").read_text(encoding="utf-8"))
report, problems = {}, []


def ff(*a):
    return subprocess.run(
        [FF, "-hide_banner", *a],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stderr


info = ff("-i", str(video))
h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info).groups()
dur = int(h) * 3600 + int(m) * 60 + float(s)
size = re.search(r"Video:.*?(\d{3,4})x(\d{3,4})", info).groups()
fps = float(re.search(r"([\d.]+) fps", info).group(1))
report.update(duration=round(dur, 2), size=f"{size[0]}x{size[1]}", fps=fps,
              audio=("48000 Hz, stereo" if "48000 Hz, stereo" in info else "?"))
if abs(dur - 60) > 0.25: problems.append(f"тривалість {dur:.2f} с")
if size != ("1080", "1920"): problems.append(f"розмір {size}")
if abs(fps - 30) > 0.01: problems.append(f"fps {fps}")
if report["audio"] == "?": problems.append("аудіо не 48 кГц стерео")

e = ff("-i", str(video), "-af", "ebur128=peak=true", "-f", "null", "-")
lufs = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", e)[-1])
tp = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", e)[-1])
report.update(loudness_lufs=lufs, true_peak_dbfs=tp)
if not -16.5 <= lufs <= -12.5: problems.append(f"гучність {lufs} LUFS")
if tp > -1.0: problems.append(f"пік {tp} dBFS")

b = ff("-i", str(video), "-vf", "blackdetect=d=0.2:pix_th=0.08", "-an", "-f", "null", "-")
blacks = [(float(x), float(y)) for x, y in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", b)]
blacks = [x for x in blacks if x[0] < dur - 0.6]            # фінальне згасання дозволене
report["black_intervals"] = blacks
if blacks: problems.append(f"чорні проміжки {blacks}")

z = ff("-i", str(video), "-vf", "freezedetect=n=-55dB:d=0.8", "-an", "-f", "null", "-")
freezes = re.findall(r"freeze_start: ([\d.]+)", z)
report["freeze_starts"] = freezes
if freezes: problems.append(f"завмерлі кадри з {freezes}")

sil = ff("-i", str(video), "-af", "silencedetect=n=-45dB:d=1.2", "-vn", "-f", "null", "-")
silences = re.findall(r"silence_start: ([\d.]+)", sil)
report["long_silences_from"] = silences
if silences: problems.append(f"паузи в звуці >1,2 с з {silences}")

if "--no-vision" not in sys.argv:
    frames = []
    for s_ in cfg["shots"]:
        t = s_["start"] + min(2.2, s_["dur"] / 2)
        p = W / "work" / f"qa_{s_['id']}.jpg"
        ff("-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(p))
        frames.append((s_["id"], p))
    led_p = W / "ledger.json"
    led = json.loads(led_p.read_text()) if led_p.exists() else {}
    vis = {}
    for sid, p in frames:
        im = Image.open(p).convert("RGB")
        im.thumbnail((640, 1136))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        prompt = ('This is a frame of a vertical video with a title label near the top and subtitles near the lower middle. '
                  'Answer ONLY JSON: {"text_cut_off": bool (any text clipped by the frame edge), '
                  '"text_overlaps_main_subject": bool, "image_distorted_or_glitchy": bool, '
                  '"main_picture_sharp": bool, "note": "max 10 words"}')
        body = {"model": "gemma3:4b", "stream": False, "format": "json", "options": {"temperature": 0, "num_ctx": 4096},
                "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(buf.getvalue()).decode()]}]}
        req = urllib.request.Request("http://127.0.0.1:11434/api/chat", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read())
        led["ollama_prompt_tokens"] = led.get("ollama_prompt_tokens", 0) + d.get("prompt_eval_count", 0)
        led["ollama_output_tokens"] = led.get("ollama_output_tokens", 0) + d.get("eval_count", 0)
        led["ollama_image_reviews"] = led.get("ollama_image_reviews", 0) + 1
        try:
            v = json.loads(d["message"]["content"])
        except Exception:
            v = {"note": "parse error"}
        vis[sid] = v
        if v.get("text_cut_off") or v.get("image_distorted_or_glitchy"):
            problems.append(f"{sid}: {v}")
    led_p.write_text(json.dumps(led, indent=1), encoding="utf-8")
    report["vision"] = vis

report["problems"] = problems
(W / "out" / "qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print("QA:", "ПРОЙДЕНО" if not problems else "ЗАУВАЖЕННЯ", "|", {k: v for k, v in report.items() if k not in ("vision",)})
for p_ in problems:
    print(" -", p_)

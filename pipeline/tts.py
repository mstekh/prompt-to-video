"""Озвучка (edge-tts, безкоштовно, без ключа) + межі слів. Слоти кадрів рахуються за РЕАЛЬНИМ мовленням.

Темп один на весь ролик: найповільніший із (+0%, +6%, +12%), за якого все вміщається в 60 с із паузою між репліками >= 0.35 с.
Оновлює start/dur у shots.json (сума рівно 60 с, надлишок розподіляється порівну між паузами).
"""
import asyncio, json, re, subprocess, sys
from pathlib import Path
import edge_tts, imageio_ffmpeg

W = Path(__file__).parent
FF = imageio_ffmpeg.get_ffmpeg_exe()
cfg = json.loads((W / "shots.json").read_text(encoding="utf-8"))
import math
VOICE, LEAD, MIN_GAP = cfg["voice"], 0.05, 0.35
MAX_TOTAL = cfg.get("max_total", 100.0)
TOTAL = MAX_TOTAL
cfg["lead"] = LEAD
PITCH = cfg.get("pitch", "+0Hz")

LEX_P = W / "lexicon.json"
LEX = json.loads(LEX_P.read_text(encoding="utf-8-sig")) if LEX_P.exists() else {}


def speakable(text):                          # словник вимови: лише для озвучки, субтитри не змінюються
    for k, v in LEX.items():
        text = re.sub(rf"(?<!\w){re.escape(k)}(?!\w)", v, text)
        text = re.sub(rf"(?<!\w){re.escape(k.capitalize())}(?!\w)", v.capitalize(), text)
    return text


def merge_lex(words):                         # «фі»+«атні» з межі слів назад в одне слово
    for v in LEX.values():
        parts = [p for p in re.split(r"[-\s]+", v) if p]
        if len(parts) < 2:
            continue
        i = 0
        while i <= len(words) - len(parts):
            if [w["w"].lower() for w in words[i:i + len(parts)]] == [p.lower() for p in parts]:
                a, b = words[i], words[i + len(parts) - 1]
                words[i:i + len(parts)] = [{"t": a["t"], "d": b["t"] + b["d"] - a["t"], "w": "".join(parts)}]
            i += 1
    return words


async def synth(text, rate, mp3):
    comm = edge_tts.Communicate(text, VOICE, rate=rate, pitch=PITCH, boundary="WordBoundary")
    words = []
    with open(mp3, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append({"t": ch["offset"] / 1e7, "d": ch["duration"] / 1e7, "w": ch["text"]})
    return words


def run_all(rate):
    out = {}
    for s in cfg["shots"]:
        mp3 = W / "audio" / f"{s['id']}.mp3"
        for attempt in range(3):
            try:
                out[s["id"]] = merge_lex(asyncio.run(synth(speakable(s["text"]), rate, mp3)))
                break
            except Exception as e:
                print(s["id"], "повтор", e, file=sys.stderr)
        else:
            sys.exit(f"{s['id']}: edge-tts не відповів")
    return out


def first_t(words):
    return min(w["t"] for w in words)


def speech_end(words):                        # тривалість самого мовлення без початкової тиші mp3
    return max(w["t"] + w["d"] for w in words) - first_t(words) + 0.1


rate_used, words_by = None, None
for rate in ("+6%", "+12%", "+18%"):
    words_by = run_all(rate)
    need = sum(speech_end(words_by[s["id"]]) + LEAD + MIN_GAP for s in cfg["shots"])
    print(f"темп {rate}: потрібно {need:.1f} с з {TOTAL}")
    rate_used = rate
    if need <= MAX_TOTAL:
        TOTAL = float(math.ceil(need))
        break

base = [speech_end(words_by[s["id"]]) + LEAD for s in cfg["shots"]]
extra = (TOTAL - sum(base)) / len(base)          # однакова пауза після кожної репліки
t = 0.0
for s, b in zip(cfg["shots"], base):
    s["dur"] = round(b + max(extra, MIN_GAP), 3)
    s["start"] = round(t, 3)
    t += s["dur"]
cfg["shots"][-1]["dur"] = round(TOTAL - cfg["shots"][-1]["start"], 3)
for s in cfg["shots"]:
    (W / "audio" / f"{s['id']}.json").write_text(
        json.dumps({"rate": rate_used, "first_t": first_t(words_by[s["id"]]), "words": words_by[s["id"]]},
                   ensure_ascii=False), encoding="utf-8")
(W / "shots.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"темп {rate_used}; пауза після реплік {max(extra, MIN_GAP):.2f} с")
for s in cfg["shots"]:
    print(f"{s['id']}: {s['start']:.2f}–{s['start']+s['dur']:.2f} (слот {s['dur']:.2f} с)")

"""Монтаж вертикального ролика з відеокліпів (clips.json) + оверлеї Pillow.

  py -3.14 build2.py --out out/money_v4.mp4 [--seconds N]
"""
import argparse, json, os, re, subprocess, sys, time
from functools import lru_cache
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
import imageio_ffmpeg

W_ = Path(__file__).parent
FF = imageio_ffmpeg.get_ffmpeg_exe()
cfg = json.loads((W_ / "shots.json").read_text(encoding="utf-8"))
SW, SH = cfg["size"]
FPS = cfg["fps"]
SHOTS = cfg["shots"]
TOTAL = sum(s["dur"] for s in SHOTS)
LEAD = cfg.get("lead", 0.2)
_LINUX_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _font_path(env_key, win_name):
    env = (os.environ.get(env_key) or "").strip()
    if env and Path(env).is_file():
        return env
    win = Path(r"C:\Windows\Fonts") / win_name
    if win.is_file():
        return str(win)
    here = Path(__file__).resolve().parent           # шрифти з пакета (Kaggle) — fonts/ поруч зі скриптом
    want = "*Black*" if "BLACK" in env_key else "*Bold*"
    found = sorted(here.rglob(f"{want}.ttf")) or sorted(here.rglob("*.ttf"))
    print(f"[fonts] {env_key}: env={env!r}, знайдено {[str(p) for p in found[:3]]}", flush=True)
    if found:
        return str(found[0])
    return _LINUX_BOLD


FONT_B = _font_path("FONT_BOLD", "segoeuib.ttf")
FONT_BLK = _font_path("FONT_BLACK", "seguibl.ttf")
GOLD, YELLOW, WHITE = (255, 205, 60), (255, 214, 10), (255, 255, 255)
SUB_W, SUB_TOP = 900, 1330
CLIPS = json.loads((W_ / "clips.json").read_text(encoding="utf-8"))

ap = argparse.ArgumentParser()
ap.add_argument("--seconds", type=float, default=TOTAL)
ap.add_argument("--out", default="out/money_v4.mp4")
args = ap.parse_args()
RENDER_DUR = min(float(args.seconds), TOTAL)


@lru_cache(None)
def font(path, size):
    return ImageFont.truetype(path, size)


def smooth(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


# ---------------------------------------------------------------- title overlay (з TitleVisual, поверх відео)
def draw_title_overlay(img, shot, tl):
    """Затемнення до 45% + золотий label / білий sublabel з fade-in, як TitleVisual."""
    img = ImageEnhance.Brightness(img).enhance(0.45)
    d = ImageDraw.Draw(img, "RGBA")
    cx, cy = SW // 2, 760
    e = smooth(tl / 0.8)
    size = 300
    while font(FONT_BLK, size).getlength(shot["label"]) > 880:
        size -= 10
    f = font(FONT_BLK, round(size * (0.92 + 0.08 * e)))
    d.text((cx, cy), shot["label"], font=f, fill=GOLD + (round(255 * e),), anchor="mm",
           stroke_width=4, stroke_fill=(0, 0, 0, round(200 * e)))
    a2 = smooth((tl - 1.2) / 0.5)
    if a2 > 0 and shot.get("sublabel"):
        d.text((cx, cy + 250), shot["sublabel"], font=font(FONT_B, 70), fill=WHITE + (round(255 * a2),),
               anchor="mm", stroke_width=4, stroke_fill=(0, 0, 0, round(200 * a2)))
    return img


# ---------------------------------------------------------------- субтитри (копія з build.py)
def tokens_with_times(shot):
    a = json.loads((W_ / "audio" / f"{shot['id']}.json").read_text(encoding="utf-8"))
    orig = [w for w in shot["text"].split() if any(ch.isalnum() for ch in w)]
    ws = a["words"]
    toks = [o if len(orig) == len(ws) else b["w"] for o, b in zip(orig, ws)] if len(orig) == len(ws) else [b["w"] for b in ws]
    ft = a.get("first_t", 0.0)
    return [{"w": tk, "t0": shot["start"] + LEAD + b["t"] - ft, "t1": shot["start"] + LEAD + b["t"] - ft + b["d"]}
            for tk, b in zip(toks, ws)]


def chunks_for(shot):
    words = tokens_with_times(shot)
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= 5 or w["w"].rstrip().endswith((".", "?", "!")) and len(cur) >= 2:
            out.append(cur)
            cur = []
    if cur:
        if out and len(cur) == 1:
            out[-1] += cur
        else:
            out.append(cur)
    res = []
    for i, c in enumerate(out):
        nxt = out[i + 1][0]["t0"] if i + 1 < len(out) else None
        end = c[-1]["t1"] + 0.4 if nxt is None else max(c[-1]["t1"], nxt - 0.02)
        res.append({"words": c, "start": c[0]["t0"] - 0.08, "end": end})
    return res


def layout(words):
    for size in (66, 58, 50, 44):
        f = font(FONT_B, size)
        sp = f.getlength(" ")
        ws = [(w, f.getlength(w["w"])) for w in words]
        total = sum(ww for _, ww in ws) + sp * (len(ws) - 1)
        if total <= SUB_W:
            return size, [ws], sp
        best = None
        for k in range(1, len(ws)):
            a, b = ws[:k], ws[k:]
            wa = sum(x for _, x in a) + sp * (len(a) - 1)
            wb = sum(x for _, x in b) + sp * (len(b) - 1)
            if max(wa, wb) <= SUB_W and (best is None or max(wa, wb) < best[0]):
                best = (max(wa, wb), [a, b])
        if best:
            return size, best[1], sp
    raise SystemExit("субтитр не вміщається у 2 рядки: " + " ".join(w["w"] for w in words))


CH = {s["id"]: chunks_for(s) for s in SHOTS}
LAYOUT = {(sid, i): layout(c["words"]) for sid, cs in CH.items() for i, c in enumerate(cs)}


def draw_sub(img, shot, T):
    for i, c in enumerate(CH[shot["id"]]):
        if c["start"] <= T < c["end"]:
            size, lines, sp = LAYOUT[(shot["id"], i)]
            f = font(FONT_B, size)
            d = ImageDraw.Draw(img, "RGBA")
            y = SUB_TOP + 78
            for line in lines:
                total = sum(ww for _, ww in line) + sp * (len(line) - 1)
                x = (SW - total) / 2
                for w, ww in line:
                    active = w["t0"] - 0.03 <= T < w["t1"] + 0.05
                    d.text((x, y), w["w"], font=f, fill=(YELLOW if active else WHITE) + (255,), anchor="ls",
                           stroke_width=7, stroke_fill=(0, 0, 0, 255))
                    x += ww + sp
                y += round(size * 1.28)
            break


def draw_label(img, shot, tl):
    if shot["kind"] == "title":
        return
    a = smooth((tl - 0.15) / 0.35)
    if a <= 0:
        return
    f = font(FONT_B, 54)
    words, lines, cur = shot["label"].split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if f.getlength(t) > 880 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines.append(cur)
    lw = max(f.getlength(l) for l in lines)
    bh = len(lines) * 70 + 20
    x0, y0 = (SW - lw) / 2 - 30, 300
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((x0, y0, x0 + lw + 60, y0 + bh), radius=26, fill=(0, 0, 0, round(150 * a)))
    d.rounded_rectangle((x0, y0, x0 + 10, y0 + bh), radius=5, fill=GOLD + (round(255 * a),))
    for j, l in enumerate(lines):
        d.text((SW / 2 + 5, y0 + 10 + 35 + j * 70), l, font=f, fill=WHITE + (round(255 * a),), anchor="mm")


# ---------------------------------------------------------------- звук
def build_voice(path, duration):
    """Голос як у build.build_audio (atrim first_t, adelay start+lead), без loudnorm."""
    inputs, parts = [], []
    for i, s in enumerate(SHOTS):
        inputs += ["-i", str(W_ / "audio" / f"{s['id']}.mp3")]
        ms = round((s["start"] + LEAD) * 1000)
        ft = json.loads((W_ / "audio" / f"{s['id']}.json").read_text(encoding="utf-8")).get("first_t", 0.0)
        parts.append(f"[{i}:a]atrim=start={ft:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
                     f"aformat=channel_layouts=stereo,adelay={ms}|{ms}[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(SHOTS)))
    fc = ";".join(parts) + f";{mix}amix=inputs={len(SHOTS)}:duration=longest:normalize=0," \
         f"apad=whole_dur={duration},atrim=0:{duration}[out]"
    r = subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", fc,
                        "-map", "[out]", "-ar", "48000", "-ac", "2", str(path)], capture_output=True, text=True)
    if r.returncode:
        sys.exit("аудіо voice: " + (r.stderr or "")[-800:])


def build_audio(path, duration):
    """Голос (+ опційна музика з ducking) і loudnorm I=-14."""
    voice = W_ / "work" / "voice_raw.wav"
    build_voice(voice, duration)
    music = cfg.get("music")
    if not music:
        r = subprocess.run(
            [FF, "-y", "-hide_banner", "-loglevel", "error", "-i", str(voice),
             "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000", "-ac", "2", str(path)],
            capture_output=True, text=True)
        if r.returncode:
            sys.exit("аудіо loudnorm: " + (r.stderr or "")[-800:])
        return
    music_p = Path(music)
    if not music_p.is_absolute():
        music_p = W_ / music_p
    if not music_p.is_file():
        sys.exit(f"music not found: {music}")
    fade_out_st = max(0.0, duration - 2.0)
    # [0]=voice key, [1]=music → sidechaincompress(music, voice), amix, loudnorm
    fc = (
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume=-18dB,"
        f"aloop=loop=-1:size=2000000000,atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d=1,afade=t=out:st={fade_out_st:.3f}:d=2[mraw];"
        f"[mraw][0:a]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[mducked];"
        f"[0:a][mducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        f"atrim=0:{duration:.3f},loudnorm=I=-14:TP=-1.5:LRA=11[out]"
    )
    r = subprocess.run(
        [FF, "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(voice), "-i", str(music_p),
         "-filter_complex", fc, "-map", "[out]", "-ar", "48000", "-ac", "2", str(path)],
        capture_output=True, text=True)
    if r.returncode:
        sys.exit("аудіо music: " + (r.stderr or "")[-800:])


# ---------------------------------------------------------------- відео-база з сегментів
COVER = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,"
         f"crop={SW}:{SH},fps={FPS},setsar=1")


def ff_run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        sys.exit("ffmpeg: " + (r.stderr or r.stdout or "")[-800:])
    return r


def make_segment(seg, need, out_path):
    """Взяти з file@start потрібний час; якщо коротше — setpts до 1.3x; інакше trim. COVER, без аудіо."""
    src = Path(seg["file"])
    if not src.is_absolute():
        src = W_ / src
    if not src.is_file():
        sys.exit(f"clip missing: {seg['file']}")
    start = float(seg["start"])
    avail = float(seg.get("dur", need))
    vf_parts = []
    if need <= avail + 1e-6:
        # trim: беремо need секунд джерела
        take = need
        pts = None
        pad = 0.0
    else:
        factor = need / avail
        if factor <= 1.3 + 1e-9:
            take = avail
            pts = factor
            pad = 0.0
        else:
            take = avail
            pts = 1.3
            pad = need - avail * 1.3
    if pts and pts > 1.0 + 1e-9:
        vf_parts.append(f"setpts=PTS*{pts:.6f}")
    vf_parts.append(COVER)
    if pad > 1e-3:
        vf_parts.append(f"tpad=stop_mode=clone:stop_duration={pad:.6f}")
    vf = ",".join(vf_parts)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FF, "-y", "-hide_banner", "-loglevel", "error", "-threads", "2",
        "-ss", f"{start:.6f}", "-i", str(src), "-t", f"{take:.6f}",
        "-vf", vf, "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-r", str(FPS), str(out_path),
    ]
    ff_run(cmd)
    # гарантія точної довжини слота (якщо трохи плаває)
    # tpad уже покриває pad; якщо вийшло довше — обріжемо при concat через -t на слоті


def probe_dur(path):
    r = subprocess.run([FF, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
    if not m:
        return 0.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def build_base_video(out_path, duration):
    """Нормалізовані сегменти → concat; точна довжина duration (pad last frame)."""
    tmp = W_ / "work" / "seg_tmp"
    if tmp.exists():
        for p in tmp.glob("*"):
            p.unlink()
    tmp.mkdir(parents=True, exist_ok=True)

    seg_files = []
    idx = 0
    built = 0.0
    for s in SHOTS:
        if built >= duration - 1e-6:
            break
        segs = CLIPS.get(s["id"]) or []
        if not segs:
            sys.exit(f"{s['id']}: немає сегментів у clips.json")
        n = len(segs)
        slot = s["dur"] / n
        shot_end = min(s["start"] + s["dur"], duration)
        for j, seg in enumerate(segs):
            if built >= duration - 1e-6:
                break
            if j == n - 1:
                need = shot_end - built
            else:
                need = min(slot, duration - built)
            if need <= 1e-6:
                break
            need = max(need, 1.0 / FPS)
            out_seg = tmp / f"{idx:04d}_{s['id']}_{j}.mp4"
            make_segment(seg, need, out_seg)
            got = probe_dur(out_seg)
            if abs(got - need) > 0.05:
                fixed = tmp / f"{idx:04d}_{s['id']}_{j}_fix.mp4"
                if got < need - 0.02:
                    pad = need - got
                    ff_run([
                        FF, "-y", "-hide_banner", "-loglevel", "error", "-threads", "2",
                        "-i", str(out_seg),
                        "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.6f},fps={FPS},setsar=1",
                        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", f"{need:.6f}", str(fixed),
                    ])
                else:
                    ff_run([
                        FF, "-y", "-hide_banner", "-loglevel", "error", "-threads", "2",
                        "-i", str(out_seg), "-t", f"{need:.6f}",
                        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-r", str(FPS), str(fixed),
                    ])
                out_seg.unlink(missing_ok=True)
                out_seg = fixed
            seg_files.append(out_seg)
            built += need
            idx += 1

    if built < duration - 1e-3 and seg_files:
        # pad останнім кадром до duration
        pad = duration - built
        last = seg_files[-1]
        padded = tmp / "zzzz_pad.mp4"
        ff_run([
            FF, "-y", "-hide_banner", "-loglevel", "error", "-threads", "2",
            "-i", str(last),
            "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.6f},fps={FPS},setsar=1",
            "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(FPS), str(padded),
        ])
        seg_files[-1] = padded
        built = duration

    lst = tmp / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in seg_files), encoding="utf-8")
    ff_run([
        FF, "-y", "-hide_banner", "-loglevel", "error", "-threads", "2",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-t", f"{duration:.6f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-an", str(out_path),
    ])
    # фінальний pad якщо concat коротший
    got = probe_dur(out_path)
    if got < duration - 0.05:
        pad = duration - got
        tmp_out = out_path.with_suffix(".pad.mp4")
        ff_run([
            FF, "-y", "-hide_banner", "-loglevel", "error", "-threads", "2",
            "-i", str(out_path),
            "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.6f},fps={FPS},setsar=1",
            "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", f"{duration:.6f}", str(tmp_out),
        ])
        tmp_out.replace(out_path)


def write_credits(out_stem):
    lines, seen = [], set()
    for s in SHOTS:
        for seg in CLIPS.get(s["id"]) or []:
            key = (seg.get("file"), seg.get("url"), seg.get("title"))
            if key in seen:
                continue
            seen.add(key)
            title = seg.get("title") or Path(seg.get("file", "")).stem
            lic = seg.get("license") or ""
            url = seg.get("url") or ""
            parts = [p for p in (title, lic, url) if p]
            lines.append(" — ".join(parts) if parts else str(seg.get("file")))
    if cfg.get("music_credit"):
        lines.append(f"Music: {cfg['music_credit']}")
    elif cfg.get("music"):
        lines.append(f"Music: {cfg['music']}")
    path = W_ / "out" / f"{out_stem}_credits.txt"
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def shot_at(T):
    i = max(j for j, s in enumerate(SHOTS) if s["start"] <= T + 1e-9)
    return i, SHOTS[i]


def main():
    (W_ / "out").mkdir(exist_ok=True)
    (W_ / "work").mkdir(exist_ok=True)
    duration = RENDER_DUR
    wav = W_ / "work" / "voice2.wav"
    base = W_ / "work" / "base2.mp4"
    print("audio…", flush=True)
    build_audio(wav, duration)
    print("base video…", flush=True)
    build_base_video(base, duration)

    out = W_ / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    stem = out.stem
    write_credits(stem)

    n = round(duration * FPS)
    frame_bytes = SW * SH * 3
    # decode base → raw rgb24 pipe
    dec = subprocess.Popen(
        [FF, "-hide_banner", "-loglevel", "error", "-i", str(base),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{SW}x{SH}", "-r", str(FPS), "-"],
        stdout=subprocess.PIPE)
    enc = subprocess.Popen(
        [FF, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{SW}x{SH}", "-r", str(FPS), "-i", "-",
         "-i", str(wav),
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-threads", "2",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
         "-t", str(duration), "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE)

    t0 = time.time()
    fade_end = TOTAL  # fade відносно повного ролика; для partial — лише якщо потрапляємо в кінець
    for f in range(n):
        raw = dec.stdout.read(frame_bytes)
        if len(raw) < frame_bytes:
            # чорний кадр, якщо база закінчилась раніше
            arr = np.zeros((SH, SW, 3), dtype=np.uint8)
        else:
            arr = np.frombuffer(raw, dtype=np.uint8).reshape((SH, SW, 3)).copy()
        frame = Image.fromarray(arr, "RGB")
        T = f / FPS
        i, s = shot_at(T)
        tl = T - s["start"]
        if s["kind"] == "title":
            frame = draw_title_overlay(frame, s, tl)
        else:
            draw_label(frame, s, tl)
        draw_sub(frame, s, T)
        # фінальний fade 0.4 с до чорного (відносно повного TOTAL)
        if T > fade_end - 0.4:
            frame = ImageEnhance.Brightness(frame).enhance(max(0.0, (fade_end - T) / 0.4))
        enc.stdin.write(frame.tobytes())
        if f % 150 == 0:
            print(f"кадр {f}/{n}  {time.time() - t0:.0f} с", flush=True)

    enc.stdin.close()
    dec.stdout.close()
    dec.wait()
    if enc.wait():
        sys.exit("ffmpeg encoder failed")
    print("готово:", out, f"{time.time() - t0:.0f} с")


if __name__ == "__main__":
    main()

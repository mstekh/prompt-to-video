#!/usr/bin/env python3
"""Розкадровка AI-кліпів: аркуш кадрів на кожен кліп + загальний аркуш, щоб очима знайти глюки.

    py clip_sheets.py work/ai_clips                    # огляд: 1 кадр/с, 5 у ряд  -> work/ai_clips/sheets/
    py clip_sheets.py work/ai_clips --detail           # детально: 3 кадри/с, 15 кадрів на кліп (d_<кліп>.jpg)
    py clip_sheets.py work/ai_clips --only s3a s6b     # лише вибрані кліпи

Результат: sheets/<кліп>.jpg (рядок кадрів одного кліпу) і sheets/_all.jpg (усі кліпи один під одним,
підпис кожного рядка — ім'я файлу). Аркуші переглядає людина або модель із зором: що шукати —
docs/03-step-by-step.md, крок 9б. Потрібен лише ffmpeg (системний або з imageio-ffmpeg).
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("Немає ffmpeg: поставте ffmpeg або `pip install imageio-ffmpeg`.")


def duration(ff, clip):
    """Тривалість кліпу в секундах з виводу `ffmpeg -i` (ffprobe є не всюди)."""
    out = subprocess.run([ff, "-i", str(clip)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def sheet(ff, clip, out, n, cols, width):
    """n кадрів, рівномірно по всій довжині кліпу, у сітку cols × рядки."""
    dur = duration(ff, clip)
    if dur <= 0:
        print(f"  ! {clip.name}: не читається (битий файл чи HTML замість mp4?)")
        return False
    rows = -(-n // cols)
    vf = f"fps={n / dur:.4f},scale={width}:-2,tile={cols}x{rows}:padding=4:color=white"
    r = subprocess.run([ff, "-v", "error", "-y", "-i", str(clip), "-vf", vf, "-frames:v", "1", "-q:v", "3", str(out)],
                       capture_output=True, text=True)
    if r.returncode or not out.exists():
        print(f"  ! {clip.name}: {r.stderr.strip()[:200]}")
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("clips_dir", type=Path)
    ap.add_argument("--out", type=Path, help="куди класти аркуші (типово <clips_dir>/sheets)")
    ap.add_argument("--detail", action="store_true", help="15 кадрів на кліп замість 5")
    ap.add_argument("--only", nargs="*", help="імена кліпів без .mp4")
    ap.add_argument("--width", type=int, default=0, help="ширина одного кадру, px")
    a = ap.parse_args()

    ff = ffmpeg_exe()
    out = a.out or a.clips_dir / "sheets"
    out.mkdir(parents=True, exist_ok=True)
    clips = sorted(a.clips_dir.glob("*.mp4"))
    if a.only:
        clips = [c for c in clips if c.stem in set(a.only)]
    if not clips:
        sys.exit(f"У {a.clips_dir} немає кліпів *.mp4")

    n, cols = (15, 5) if a.detail else (5, 5)
    width = a.width or (300 if a.detail else 220)
    prefix = "d_" if a.detail else ""
    done = []
    for c in clips:
        f = out / f"{prefix}{c.stem}.jpg"
        if sheet(ff, c, f, n, cols, width):
            done.append((c.stem, f))
            print(f"  {c.stem}: {f}")

    if done and not a.detail:
        # загальний аркуш: рядки кліпів один під одним, ліворуч підпис (drawtext може не мати шрифту, тож підпис — смуга з назвою у файлі _all.txt)
        allf = out / "_all.jpg"
        inputs = []
        for _, f in done:
            inputs += ["-i", str(f)]
        stack = "".join(f"[{i}:v]" for i in range(len(done))) + f"vstack=inputs={len(done)}" if len(done) > 1 else "null"
        subprocess.run([ff, "-v", "error", "-y", *inputs, "-filter_complex", stack, "-q:v", "3", str(allf)], check=False)
        (out / "_all.txt").write_text("\n".join(f"{i + 1}. {s}" for i, (s, _) in enumerate(done)) + "\n", encoding="utf-8")
        print(f"Загальний аркуш: {allf} (порядок рядків — {out / '_all.txt'})")


if __name__ == "__main__":
    main()

"""Scene-cut candidate 3s segments + JPEG thumbs from src/S*/ videos."""
import json, re, subprocess
from pathlib import Path
import imageio_ffmpeg

W = Path(__file__).parent
FF = imageio_ffmpeg.get_ffmpeg_exe()
SRC, WORK = W / "src", W / "work"
THUMBS = WORK / "thumbs"
SHOT_MIN, SEG_DUR, SEG_PAD, SEG_STEP, MAX_SEGS = 3.4, 3.0, 0.3, 3.2, 6
THUMB_OFFS = (0.2, 1.5, 2.8)
THUMB_LETTERS = "abc"
SCENE_RE = re.compile(r"pts_time:([\d.]+)")
SCALE = "scale='if(gte(iw,ih),384,-2)':'if(gte(iw,ih),-2,384)'"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def duration(path):
    r = run([FF, "-hide_banner", "-i", str(path)])
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        return 0.0
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


def scene_cuts(path):
    r = run([
        FF, "-hide_banner", "-threads", "2", "-i", str(path),
        "-filter:v", "select='gt(scene,0.3)',showinfo", "-f", "null", "-",
    ])
    times = [float(x) for x in SCENE_RE.findall(r.stderr)]
    return sorted(set(times))


def shot_ranges(cuts, dur):
    bounds = [0.0] + [t for t in cuts if 0 < t < dur] + [dur]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def candidates(ranges):
    out = []
    for a, b in ranges:
        if b - a < SHOT_MIN:
            continue
        t = a + SEG_PAD
        while t + SEG_DUR <= b + 1e-6 and len(out) < MAX_SEGS:
            out.append(round(t, 3))
            t += SEG_STEP
        if len(out) >= MAX_SEGS:
            break
    return out[:MAX_SEGS]


def extract_thumb(video, t, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = run([
        FF, "-y", "-hide_banner", "-threads", "2", "-ss", f"{t:.3f}",
        "-i", str(video), "-frames:v", "1", "-vf", SCALE, "-q:v", "3", str(dest),
    ])
    if r.returncode or not dest.exists():
        raise RuntimeError(f"thumb fail {dest.name}: {(r.stderr or '')[-200:]}")


def resolve_file(entry, shot_dir):
    f = Path(entry.get("file") or "")
    if f.is_file():
        return f
    vid = entry.get("id", "")
    for p in shot_dir.glob(f"{vid}.*"):
        if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}:
            return p
    return None


def main():
    THUMBS.mkdir(parents=True, exist_ok=True)
    segments = []
    counts = {}
    for shot_dir in sorted(SRC.glob("S*")):
        if not shot_dir.is_dir():
            continue
        shot = shot_dir.name
        meta_path = shot_dir / "meta.json"
        if not meta_path.exists():
            print(f"{shot}: 0 (no meta.json)")
            counts[shot] = 0
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        n_shot = 0
        for entry in meta:
            video = resolve_file(entry, shot_dir)
            if video is None:
                print(f"  skip missing file {entry.get('id')}")
                continue
            vid = entry.get("id") or video.stem
            dur = duration(video)
            if dur <= 0:
                print(f"  skip bad duration {vid}")
                continue
            starts = candidates(shot_ranges(scene_cuts(video), dur))
            for i, start in enumerate(starts):
                seg = f"{shot}/{vid}_{i}"
                rel_thumbs = []
                for letter, off in zip(THUMB_LETTERS, THUMB_OFFS):
                    rel = f"{shot}/{vid}_{i}_{letter}.jpg"
                    extract_thumb(video, start + off, THUMBS / rel)
                    rel_thumbs.append(rel.replace("\\", "/"))
                segments.append({
                    "seg": seg,
                    "shot": shot,
                    "file": str(video),
                    "start": start,
                    "dur": SEG_DUR,
                    "thumbs": rel_thumbs,
                    "title": entry.get("title", ""),
                    "license": entry.get("license", ""),
                    "url": entry.get("url", ""),
                })
                n_shot += 1
        counts[shot] = n_shot
        print(f"{shot}: {n_shot}")
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "segments.json").write_text(
        json.dumps(segments, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"total: {len(segments)} -> work/segments.json")


if __name__ == "__main__":
    main()

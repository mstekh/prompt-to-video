import os
"""Upload segments to Kaggle, run VL review kernel, build clips.json."""
import json, math, shutil, subprocess, time
from pathlib import Path

W = Path(__file__).parent
WORK = W / "work"
DS_DIR = WORK / "kaggle_ds"
KER_DIR = WORK / "kaggle_kernel"
OUT_DIR = WORK / "review_out"
import shutil as _sh
KAGGLE = Path(_sh.which("kaggle") or Path.home() / "AppData/Roaming/Python/Python312/Scripts/kaggle.exe")
def _kaggle_user():
    """Логін Kaggle поточного користувача: KAGGLE_USERNAME → ~/.kaggle/kaggle.json → `kaggle config view`."""
    import os, json, re, subprocess
    from pathlib import Path as _P
    if os.environ.get("KAGGLE_USERNAME"):
        return os.environ["KAGGLE_USERNAME"]
    kj = _P.home() / ".kaggle" / "kaggle.json"
    if kj.is_file():
        return json.loads(kj.read_text(encoding="utf-8"))["username"]
    out = subprocess.run([str(KAGGLE), "config", "view"], capture_output=True, text=True, encoding="utf-8",
                         env={**os.environ, "PYTHONUTF8": "1"}).stdout
    m = re.search(r"username:\s*(\S+)", out)
    if not m:
        raise SystemExit("не вдалося визначити логін Kaggle")
    return m.group(1)


KAGGLE_USER = _kaggle_user()
DS_ID = f"{KAGGLE_USER}/money-segments"
KER_ID = f"{KAGGLE_USER}/money-review"
POLL_S, MAX_MIN = 30, 60


def sh(args, check=True):
    print("+", " ".join(str(a) for a in args), flush=True)
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})  # Kaggle CLI інакше падає на cp1251
    if r.stdout:
        print(r.stdout.rstrip(), flush=True)
    if r.stderr:
        print(r.stderr.rstrip(), flush=True)
    if check and r.returncode:
        raise SystemExit(f"cmd failed ({r.returncode}): {args}")
    return r


def build_dataset():
    if DS_DIR.exists():
        shutil.rmtree(DS_DIR)
    DS_DIR.mkdir(parents=True)
    segs_src = WORK / "segments.json"
    if not segs_src.exists():
        raise SystemExit("missing work/segments.json — run cut_segments.py first")
    shutil.copy2(segs_src, DS_DIR / "segments.json")

    shots = json.loads((W / "shots.json").read_text(encoding="utf-8"))
    subjects = {}
    for s in shots.get("shots", []):
        sid = s["id"]
        subjects[sid] = s.get("subject_en") or s.get("label") or sid
    (DS_DIR / "shots_subjects.json").write_text(
        json.dumps(subjects, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    thumbs_src = WORK / "thumbs"
    thumbs_dst = DS_DIR / "thumbs"
    if thumbs_src.is_dir():
        shutil.copytree(thumbs_src, thumbs_dst)
    else:
        thumbs_dst.mkdir()

    meta = {
        "title": "money-segments",
        "id": DS_ID,
        "licenses": [{"name": "CC0-1.0"}],
        "isPrivate": True,
    }
    (DS_DIR / "dataset-metadata.json").write_text(
        json.dumps(meta, indent=1), encoding="utf-8"
    )


def push_dataset():
    r = sh([KAGGLE, "datasets", "create", "-p", str(DS_DIR), "--dir-mode", "zip"], check=False)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 and "error" not in out.lower():
        return
    low = out.lower()
    if "already" in low or "exists" in low or r.returncode != 0:
        sh([KAGGLE, "datasets", "version", "-p", str(DS_DIR), "-m", "segments update", "--dir-mode", "zip"])
    # нова версія датасету стає доступною не одразу — інакше ядро бачить стару
    import time
    for _ in range(40):
        s = sh([KAGGLE, "datasets", "status", DS_ID], check=False)
        if "ready" in ((s.stdout or "") + (s.stderr or "")).lower():
            break
        time.sleep(15)
    time.sleep(120)


def push_kernel():
    if KER_DIR.exists():
        shutil.rmtree(KER_DIR)
    KER_DIR.mkdir(parents=True)
    shutil.copy2(W / "review_kernel.py", KER_DIR / "review_kernel.py")
    meta = {
        "id": KER_ID,
        "title": "money-review",
        "code_file": "review_kernel.py",
        "language": "python",
        "kernel_type": "script",
        "is_private": True,
        "enable_gpu": True,
        "enable_internet": True,
        "dataset_sources": [DS_ID],
        "competition_sources": [],
        "kernel_sources": [],
    }
    (KER_DIR / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    sh([KAGGLE, "kernels", "push", "-p", str(KER_DIR)])


def wait_kernel():
    deadline = time.time() + MAX_MIN * 60
    while time.time() < deadline:
        r = sh([KAGGLE, "kernels", "status", KER_ID], check=False)
        text = ((r.stdout or "") + "\n" + (r.stderr or "")).lower()
        print(f"status: {(r.stdout or r.stderr or '').strip()[:200]}", flush=True)
        if "complete" in text or "success" in text:
            return
        if "error" in text or "failed" in text or "cancel" in text:
            raise SystemExit(f"kernel failed: {(r.stdout or '') + (r.stderr or '')}")
        time.sleep(POLL_S)
    raise SystemExit(f"kernel timeout after {MAX_MIN} min")


def download_output():
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    sh([KAGGLE, "kernels", "output", KER_ID, "-p", str(OUT_DIR)])


def video_key(seg_entry):
    seg = seg_entry["seg"]  # S2/videoid_n
    name = seg.split("/", 1)[-1]
    if "_" in name:
        return name.rsplit("_", 1)[0]
    return name


def build_clips():
    segs = json.loads((WORK / "segments.json").read_text(encoding="utf-8"))
    reviews_path = OUT_DIR / "reviews.json"
    if not reviews_path.exists():
        found = list(OUT_DIR.rglob("reviews.json"))
        if not found:
            raise SystemExit("reviews.json not in kernel output")
        reviews_path = found[0]
    reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
    shots = json.loads((W / "shots.json").read_text(encoding="utf-8"))
    shot_dur = {s["id"]: float(s.get("dur", 0)) for s in shots.get("shots", [])}

    clips = {}
    lacking = []
    shot_ids = sorted({s["shot"] for s in segs})
    for shot in shot_ids:
        need = max(1, math.ceil(shot_dur.get(shot, 0) / 2.6)) if shot_dur.get(shot) else 1
        cands = []
        for s in segs:
            if s["shot"] != shot:
                continue
            rev = reviews.get(s["seg"]) or {}
            if int(rev.get("match", 0)) < 5:
                continue
            if rev.get("text_or_watermark"):
                continue
            if not rev.get("motion_ok"):
                continue
            score = 2 * int(rev.get("match", 0)) + int(rev.get("quality", 0))
            cands.append((score, s, rev))
        cands.sort(key=lambda x: -x[0])
        picked, per_video = [], {}
        for score, s, rev in cands:
            vk = video_key(s)
            if per_video.get(vk, 0) >= 2:
                continue
            picked.append({
                "file": s["file"],
                "start": s["start"],
                "dur": s["dur"],
                "title": s.get("title", ""),
                "license": s.get("license", ""),
                "url": s.get("url", ""),
            })
            per_video[vk] = per_video.get(vk, 0) + 1
            if len(picked) >= need:
                break
        clips[shot] = picked
        if len(picked) < need:
            lacking.append(f"{shot} have={len(picked)} need={need}")

    (W / "clips.json").write_text(json.dumps(clips, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote clips.json ({sum(len(v) for v in clips.values())} clips)", flush=True)
    if lacking:
        print("LACKING COVERAGE:", "; ".join(lacking), flush=True)
    else:
        print("coverage OK for all shots with segments", flush=True)


def main():
    if not KAGGLE.is_file():
        raise SystemExit(f"kaggle CLI not found: {KAGGLE}")
    build_dataset()
    push_dataset()
    push_kernel()
    wait_kernel()
    download_output()
    build_clips()


if __name__ == "__main__":
    main()

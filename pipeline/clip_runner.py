"""Один AI-кліп на Kaggle: стартове зображення + промпт руху -> 5-секундне відео 768x1344 (MiniMax H3, image-to-video).

  py clip_runner.py --image start.png --prompt "Slow push-in ..." --name s2a --out work/ai_clips [--seconds 5]

Ядро (kernels/video_h3/kernel.py) самодостатнє: ставить ComfyUI і ноди з закріплених комітів, тягне ваги моделі з Hugging Face,
запускає граф і пише video.mp4 + results.json. Одна задача = один запуск Kaggle (≈20-25 хв GPU T4).
Статус: 🧪 написано за протоколом перевіреного приватного виконавця; перший запуск у цьому репозиторії — на вас.
Логін Kaggle береться з KAGGLE_USERNAME, ~/.kaggle/kaggle.json або `kaggle config view`; нічого не вписано в код.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time, uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
KDIR = (HERE / "kernels" / "video_h3") if (HERE / "kernels").is_dir() else (HERE.parent / "kernels" / "video_h3")
ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
SLUG = "pipeline-video"


def kaggle_cli():
    return shutil.which("kaggle") or sys.exit("kaggle CLI не знайдено: py -m pip install --user kaggle")


def sh(args, check=True):
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
    if check and r.returncode:
        sys.exit(f"помилка {r.returncode}: {' '.join(map(str, args))}\n{(r.stderr or r.stdout)[-1500:]}")
    return r


def kaggle_user(k):
    if os.environ.get("KAGGLE_USERNAME"):
        return os.environ["KAGGLE_USERNAME"]
    kj = Path.home() / ".kaggle" / "kaggle.json"
    if kj.is_file():
        return json.loads(kj.read_text(encoding="utf-8"))["username"]
    m = re.search(r"username:\s*(\S+)", sh([k, "config", "view"]).stdout)
    return m.group(1) if m else sys.exit("не вдалося визначити логін Kaggle")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", default="work/ai_clips")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--timeout-min", type=int, default=75)
    a = ap.parse_args()
    k = kaggle_cli()
    user = kaggle_user(k)
    ds, ker = f"{user}/{SLUG}-in", f"{user}/{SLUG}"
    job_id, batch = time.strftime("%Y%m%d-%H%M") + "-" + uuid.uuid4().hex[:6], uuid.uuid4().hex[:12]
    work = Path("work") / "clip_build" / a.name
    shutil.rmtree(work, ignore_errors=True)
    data, kern = work / "data", work / "kernel"
    data.mkdir(parents=True); kern.mkdir(parents=True)

    # 1) датасет: job.json, вхідне зображення, граф ComfyUI, rwa.py, manifest.json
    img = Path(a.image)
    (data / "job.json").write_text(json.dumps({"job_id": job_id, "batch_id": batch, "type": "video", "params": {
        "prompt": a.prompt, "seconds": a.seconds, "orientation": "portrait", "seed": a.seed, "name": a.name}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(img, data / f"input{img.suffix}")
    for f in ("workflow-i2v-h3.json", "rwa.py"):
        shutil.copy2(KDIR / f, data / f)
    names = ["job.json", f"input{img.suffix}", "workflow-i2v-h3.json", "rwa.py"]
    manifest = json.dumps({"batch_id": batch, "job_id": job_id, "files": {n: sha(data / n) for n in names}},
                          ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    (data / "manifest.json").write_bytes(manifest)
    (data / "dataset-metadata.json").write_text(json.dumps({"title": f"{SLUG}-in", "id": ds, "licenses": [{"name": "CC0-1.0"}]}),
                                                encoding="utf-8")
    exists = sh([k, "datasets", "status", ds], check=False).returncode == 0
    sh([k, "datasets", "version" if exists else "create", "-p", data] + (["-m", "clip"] if exists else []) + ["--dir-mode", "zip"])
    for _ in range(60):                                   # очікування готовності (403 від API трапляється — тоді просто чекаємо)
        r = sh([k, "datasets", "status", ds], check=False)
        text = (r.stdout + r.stderr).lower()
        if "ready" in text:
            break
        if "403" in text or "forbidden" in text:
            time.sleep(120); break
        time.sleep(15)

    # 2) ядро
    code = (KDIR / "kernel.py").read_text(encoding="utf-8").replace("__BATCH_ID__", batch).replace(
        "__MANIFEST_SHA__", hashlib.sha256(manifest).hexdigest())
    (kern / "kernel.py").write_text(code, encoding="utf-8")
    (kern / "kernel-metadata.json").write_text(json.dumps({
        "id": ker, "title": SLUG, "code_file": "kernel.py", "language": "python", "kernel_type": "script",
        "is_private": True, "enable_gpu": True, "enable_internet": True, "machine_shape": "NvidiaTeslaT4",
        "dataset_sources": [ds], "competition_sources": [], "kernel_sources": []}, indent=2), encoding="utf-8")
    sh([k, "kernels", "push", "-p", kern])
    deadline = time.time() + a.timeout_min * 60
    while time.time() < deadline:
        m = re.search(r"KernelWorkerStatus\.(\w+)", sh([k, "kernels", "status", ker], check=False).stdout)
        st = m.group(1) if m else "UNKNOWN"
        print(time.strftime("%H:%M"), st, flush=True)
        if st in ("COMPLETE", "ERROR", "CANCEL_ACKNOWLEDGED", "CANCELLED"):
            break
        time.sleep(60)

    # 3) результат
    outdir = work / "output"
    outdir.mkdir()
    sh([k, "kernels", "output", ker, "-p", outdir, "--force"], check=False)    # на Windows CLI інколи падає на кодуванні вже після завантаження
    vids = sorted(outdir.rglob("video.mp4")) or sorted(outdir.rglob("*.mp4"))
    if not vids:
        sys.exit(f"відео не знайдено в результаті; дивіться {outdir}/results.json і лог ядра")
    dst = Path(a.out)
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(vids[0], dst / f"{a.name}.mp4")
    print("готово:", dst / f"{a.name}.mp4")


if __name__ == "__main__":
    main()

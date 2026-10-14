"""Generic Kaggle job runner: upload dataset, push kernel, poll, download output.

  py -3.14 kaggle_job.py --name <slug> --script <kernel_script.py> --data <local_folder>
                       [--gpu] [--timeout-min 90] --out <local_out_folder>
"""
from __future__ import annotations

import argparse
import json
import re
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

W = Path(__file__).resolve().parent
DEFAULT_KAGGLE = Path(shutil.which("kaggle") or "kaggle")


def kaggle_username() -> str:
    env = (os.environ.get("KAGGLE_USERNAME") or "").strip()
    if env:
        return env
    cfg = Path.home() / ".kaggle" / "kaggle.json"
    if not cfg.is_file():                       # новий формат токена (access_token): логін бере сам CLI
        try:
            r = subprocess.run([str(find_kaggle()), "config", "view"], capture_output=True, text=True,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=60)
            m = re.search(r"username:\s*(\S+)", r.stdout)
            if m and m.group(1) not in ("None", ""):
                return m.group(1)
        except Exception:
            pass
        sys.exit("Kaggle username: set KAGGLE_USERNAME or create ~/.kaggle/kaggle.json")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    user = (data.get("username") or "").strip()
    if not user:
        sys.exit("kaggle.json has no username")
    return user


def find_kaggle() -> Path:
    if DEFAULT_KAGGLE.is_file():
        return DEFAULT_KAGGLE
    which = shutil.which("kaggle")
    if which:
        return Path(which)
    sys.exit(f"kaggle CLI not found (tried {DEFAULT_KAGGLE} and PATH)")


def cli_env() -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def sh(kaggle: Path, args: list, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [str(kaggle), *[str(a) for a in args]]
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=cli_env(),
    )
    if r.stdout:
        print(r.stdout.rstrip(), flush=True)
    if r.stderr:
        print(r.stderr.rstrip(), flush=True)
    if check and r.returncode:
        sys.exit(f"kaggle failed ({r.returncode}): {' '.join(cmd)}")
    return r


def upload_dataset(kaggle: Path, user: str, slug: str, data_dir: Path) -> str:
    """Create or version private dataset <user>/<slug>-in from data_dir."""
    ds_id = f"{user}/{slug}-in"
    data_dir = data_dir.resolve()
    if not data_dir.is_dir():
        sys.exit(f"--data is not a directory: {data_dir}")

    meta = {
        "title": f"{slug}-in",
        "id": ds_id,
        "licenses": [{"name": "CC0-1.0"}],
    }
    (data_dir / "dataset-metadata.json").write_text(
        json.dumps(meta, indent=1), encoding="utf-8"
    )

    st = sh(kaggle, ["datasets", "status", ds_id], check=False)
    text = ((st.stdout or "") + "\n" + (st.stderr or "")).lower()
    exists = st.returncode == 0 and "404" not in text and "not found" not in text

    if exists:
        sh(
            kaggle,
            ["datasets", "version", "-p", str(data_dir), "-m", "run", "--dir-mode", "zip"],
        )
    else:
        r = sh(
            kaggle,
            ["datasets", "create", "-p", str(data_dir), "--dir-mode", "zip"],
            check=False,
        )
        out = ((r.stdout or "") + "\n" + (r.stderr or "")).lower()
        if r.returncode != 0:
            if "already" in out or "exists" in out:
                sh(
                    kaggle,
                    [
                        "datasets",
                        "version",
                        "-p",
                        str(data_dir),
                        "-m",
                        "run",
                        "--dir-mode",
                        "zip",
                    ],
                )
            else:
                sys.exit(f"datasets create failed: {(r.stdout or '') + (r.stderr or '')}")
    return ds_id


def wait_dataset(kaggle: Path, ds_id: str, timeout_min: int) -> None:
    deadline = time.time() + timeout_min * 60
    while time.time() < deadline:
        r = sh(kaggle, ["datasets", "status", ds_id], check=False)
        text = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
        low = text.lower()
        print(f"dataset status: {text[:240]}", flush=True)
        if "403" in low or "forbidden" in low:      # Kaggle API інколи відмовляє на GetDatasetStatus — чекаємо й вважаємо готовим
            print("status API 403: чекаю 120 с і продовжую", flush=True)
            time.sleep(120)
            return
        if "error" in low or "failed" in low:
            sys.exit(f"dataset not ready: {text}")
        if "ready" in low or "complete" in low:
            return
        if r.returncode == 0 and "404" not in low and "not found" not in low:
            if "processing" not in low and "pending" not in low:
                return
        time.sleep(30)
    sys.exit(f"dataset timeout after {timeout_min} min: {ds_id}")


def write_kernel(user: str, slug: str, script: Path, ds_id: str, gpu: bool) -> Path:
    ker_dir = W / "work" / "kjobs" / slug
    if ker_dir.exists():
        shutil.rmtree(ker_dir)
    ker_dir.mkdir(parents=True)

    code_name = script.name
    shutil.copy2(script, ker_dir / code_name)
    meta = {
        "id": f"{user}/{slug}",
        "title": slug,
        "code_file": code_name,
        "language": "python",
        "kernel_type": "script",
        "is_private": True,
        "enable_gpu": bool(gpu),
        "enable_internet": True,
        "dataset_sources": [ds_id],
        "competition_sources": [],
        "kernel_sources": [],
    }
    (ker_dir / "kernel-metadata.json").write_text(
        json.dumps(meta, indent=1), encoding="utf-8"
    )
    return ker_dir


def wait_kernel(kaggle: Path, ker_id: str, timeout_min: int) -> str:
    deadline = time.time() + timeout_min * 60
    last = ""
    while time.time() < deadline:
        r = sh(kaggle, ["kernels", "status", ker_id], check=False)
        text = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
        last = text
        low = text.lower()
        print(f"kernel status: {text[:240]}", flush=True)
        if "complete" in low or "success" in low:
            return text
        if "error" in low or "failed" in low or "cancel" in low:
            sys.exit(f"kernel failed: {text}")
        time.sleep(30)
    sys.exit(f"kernel timeout after {timeout_min} min; last={last}")


def download_output(kaggle: Path, ker_id: str, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sh(kaggle, ["kernels", "output", ker_id, "-p", str(out_dir), "--force"])
    return sorted(p for p in out_dir.rglob("*") if p.is_file())


def main() -> None:
    ap = argparse.ArgumentParser(description="Run a script kernel on Kaggle")
    ap.add_argument("--name", required=True, help="slug for dataset/kernel ids")
    ap.add_argument("--script", required=True, help="local kernel .py to push")
    ap.add_argument("--data", required=True, help="local folder uploaded as dataset")
    ap.add_argument("--out", required=True, help="local folder for kernels output")
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--timeout-min", type=int, default=90)
    args = ap.parse_args()

    slug = args.name.strip().lower().replace(" ", "-")
    script = Path(args.script).resolve()
    data = Path(args.data).resolve()
    out = Path(args.out).resolve()
    if not script.is_file():
        sys.exit(f"script not found: {script}")

    user = kaggle_username()
    kaggle = find_kaggle()
    print(f"user={user} slug={slug} gpu={args.gpu}", flush=True)

    ds_id = upload_dataset(kaggle, user, slug, data)
    wait_dataset(kaggle, ds_id, args.timeout_min)

    ker_dir = write_kernel(user, slug, script, ds_id, args.gpu)
    ker_id = f"{user}/{slug}"
    sh(kaggle, ["kernels", "push", "-p", str(ker_dir)])

    status = wait_kernel(kaggle, ker_id, args.timeout_min)
    files = download_output(kaggle, ker_id, out)

    print("FINAL STATUS:", status, flush=True)
    print("DOWNLOADED:", flush=True)
    for p in files:
        print(" ", p, flush=True)
    if not files:
        print("  (none)", flush=True)


if __name__ == "__main__":
    main()

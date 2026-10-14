"""Kaggle CPU kernel: render final MP4 from render_bundle via build2.py + qa.py.

Dataset mounted under /kaggle/input/<slug>-in/ (or one nested subfolder).
Keeps only out/final.mp4, out/qa.json, out/*credits*.txt.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

OUT = Path("/kaggle/working")
JOB = OUT / "job"
KEEP_NAMES = {"final.mp4", "qa.json"}


def resolve_input() -> Path:
    """Тека з build2.py у будь-якій глибині /kaggle/input (Kaggle змінює схему монтування)."""
    hits = sorted(Path("/kaggle/input").rglob("build2.py"))
    if not hits:
        raise FileNotFoundError("build2.py not found under /kaggle/input")
    return hits[0].parent


def need_install(pkg: str, mod: str | None = None) -> bool:
    name = mod or pkg.replace("-", "_")
    return importlib.util.find_spec(name) is None


def ensure_deps() -> None:
    want = []
    for pkg, mod in (
        ("edge-tts", "edge_tts"),
        ("imageio-ffmpeg", "imageio_ffmpeg"),
        ("pillow", "PIL"),
        ("numpy", "numpy"),
    ):
        if need_install(pkg, mod):
            want.append(pkg)
    if not want:
        print("deps ok", flush=True)
        return
    print("pip install", want, flush=True)
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", *want],
        capture_output=True,
        text=True,
    )
    if r.returncode:
        raise RuntimeError("pip failed:\n" + (r.stderr or r.stdout or "")[-2000:])


def run(cmd: list[str], cwd: Path) -> None:
    print("+", " ".join(cmd), flush=True)
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    fonts = cwd / "fonts"
    bold = fonts / "DejaVuSans-Bold.ttf"
    if not bold.is_file():
        # any *Bold*.ttf / segoeuib
        cands = list(fonts.glob("*Bold*.ttf")) + list(fonts.glob("segoeuib.ttf"))
        bold = cands[0] if cands else bold
    black = fonts / "seguibl.ttf"
    if not black.is_file():
        cands = list(fonts.glob("*Black*.ttf")) + list(fonts.glob("seguibl.ttf"))
        black = cands[0] if cands else bold
    if bold.is_file():
        env["FONT_BOLD"] = str(bold)
    if black.is_file():
        env["FONT_BLACK"] = str(black)
    elif bold.is_file():
        env["FONT_BLACK"] = str(bold)
    r = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True)
    if r.stdout:
        print(r.stdout[-4000:], flush=True)
    if r.stderr:
        print(r.stderr[-4000:], flush=True)
    if r.returncode:
        raise RuntimeError(f"cmd failed ({r.returncode}): {' '.join(cmd)}")


def keep_outputs(job: Path) -> None:
    out = job / "out"
    kept: list[Path] = []
    if out.is_dir():
        for p in out.iterdir():
            if not p.is_file():
                continue
            if p.name in KEEP_NAMES or (
                "credits" in p.name.lower() and p.suffix.lower() == ".txt"
            ):
                dest = OUT / p.name
                shutil.copy2(p, dest)
                kept.append(dest)
    # wipe job copy
    if job.exists():
        shutil.rmtree(job, ignore_errors=True)
    print("kept:", [str(p) for p in kept], flush=True)
    if not (OUT / "final.mp4").is_file():
        raise FileNotFoundError("final.mp4 missing after render")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("python", sys.version, flush=True)
    try:
        ensure_deps()
        inp = resolve_input()
        print("input:", inp, flush=True)
        if JOB.exists():
            shutil.rmtree(JOB)
        shutil.copytree(inp, JOB)
        # drop kaggle metadata if present
        (JOB / "dataset-metadata.json").unlink(missing_ok=True)

        run([sys.executable, "build2.py", "--out", "out/final.mp4"], JOB)
        run([sys.executable, "qa.py", "out/final.mp4", "--no-vision"], JOB)
        keep_outputs(JOB)
        print("done", flush=True)
    except Exception:
        err = traceback.format_exc()
        print(err, flush=True)
        (OUT / "error.txt").write_text(err, encoding="utf-8")
        raise


if __name__ == "__main__":
    main()

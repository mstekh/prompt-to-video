"""Kaggle GPU kernel: one instrumental track via ACE-Step (Apache-2.0).

Input: /kaggle/input/<slug>-in[/nested]/music.json
  {"prompt": "...", "seconds": 100}

Outputs: /kaggle/working/music.wav, music.mp3, music_meta.json
On failure: /kaggle/working/error.txt

ACE-Step pins spacy==3.8.4 (Requires-Python <3.13). Kaggle now ships
Python 3.13, so we install into an isolated uv venv with Python 3.11.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import traceback
from pathlib import Path

OUT = Path("/kaggle/working")
ACE_VENV = Path("/tmp/ace")
ACE_PY = ACE_VENV / "bin" / "python"
GEN_SCRIPT = Path("/tmp/gen_ace.py")
DEFAULT_PROMPT = (
    "calm modern documentary background, minimal piano and soft synth pads, "
    "light steady pulse, no vocals"
)

GEN_ACE_SRC = r'''
"""Run ACE-Step generation inside the Python 3.11 uv venv."""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import traceback
from pathlib import Path

OUT = Path("/kaggle/working")
DEFAULT_PROMPT = (
    "calm modern documentary background, minimal piano and soft synth pads, "
    "light steady pulse, no vocals"
)


def resolve_input() -> Path:
    base = Path("/kaggle/input")
    if not base.is_dir():
        raise FileNotFoundError("/kaggle/input missing")
    roots = sorted(p for p in base.iterdir() if p.is_dir())
    if not roots:
        raise FileNotFoundError("no datasets under /kaggle/input")
    root = roots[0]
    files = [
        p
        for p in root.iterdir()
        if p.is_file() and p.name != "dataset-metadata.json"
    ]
    dirs = [p for p in root.iterdir() if p.is_dir()]
    if not files and len(dirs) == 1:
        return dirs[0]
    return root


def find_ffmpeg() -> str:
    from shutil import which

    p = which("ffmpeg")
    if p:
        return p
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


LYRICS = "[instrumental]"


def load_cfg(inp: Path) -> tuple[str, float]:
    cfg_path = inp / "music.json"
    if not cfg_path.is_file():
        found = list(inp.rglob("music.json"))
        if found:
            cfg_path = found[0]
        else:
            return DEFAULT_PROMPT, 100.0
    data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    prompt = (data.get("prompt") or DEFAULT_PROMPT).strip() or DEFAULT_PROMPT
    seconds = float(data.get("seconds", 100))
    global LYRICS
    LYRICS = (data.get("lyrics") or "[instrumental]").strip()
    return prompt, seconds


def wav_to_mp3(wav: Path, mp3: Path) -> None:
    ff = find_ffmpeg()
    r = subprocess.run(
        [
            ff,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(wav),
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(mp3),
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode:
        raise RuntimeError("ffmpeg mp3: " + (r.stderr or r.stdout or "")[-800:])


def pick_generated_wav(save_dir: Path, before: set[str]) -> Path:
    candidates = []
    for p in save_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".wav", ".flac", ".mp3"):
            continue
        if str(p.resolve()) in before:
            continue
        candidates.append(p)
    if not candidates:
        candidates = [
            p
            for p in save_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in (".wav", ".flac", ".mp3")
        ]
    if not candidates:
        raise FileNotFoundError("ACE-Step produced no audio file")
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("python", sys.version, flush=True)
    import torch

    print("torch", torch.__version__, "cuda", torch.cuda.is_available(), flush=True)

    import acestep

    print("acestep", getattr(acestep, "__version__", "?"), flush=True)

    from acestep.pipeline_ace_step import ACEStepPipeline

    inp = resolve_input()
    print("input:", inp, flush=True)
    prompt, seconds = load_cfg(inp)
    seed = int(os.environ.get("MUSIC_SEED", random.randint(1, 2**31 - 1)))
    print(f"prompt={prompt!r} seconds={seconds} seed={seed}", flush=True)

    model = ACEStepPipeline(
        checkpoint_dir=None,
        dtype="float32",
        torch_compile=False,
        cpu_offload=True,
        overlapped_decode=True,
    )
    print(model, flush=True)

    before = {str(p.resolve()) for p in OUT.rglob("*") if p.is_file()}
    result = model(
        audio_duration=float(seconds),
        prompt=prompt,
        lyrics=LYRICS,
        infer_step=27,
        guidance_scale=15.0,
        scheduler_type="euler",
        cfg_type="apg",
        omega_scale=10.0,
        manual_seeds=str(seed),
        guidance_interval=0.5,
        guidance_interval_decay=0.0,
        min_guidance_scale=3.0,
        use_erg_tag=True,
        use_erg_lyric=False,
        use_erg_diffusion=True,
        oss_steps=[],
        guidance_scale_text=0.0,
        guidance_scale_lyric=0.0,
        save_path=str(OUT),
        format="wav",
    )
    src = None
    if isinstance(result, (list, tuple)):
        for item in result:
            if isinstance(item, str) and item.lower().endswith(
                (".wav", ".flac", ".mp3")
            ):
                src = Path(item)
                break
    if src is None or not src.is_file():
        src = pick_generated_wav(OUT, before)
    wav = OUT / "music.wav"
    mp3 = OUT / "music.mp3"
    if src.resolve() != wav.resolve():
        if src.suffix.lower() == ".wav":
            wav.write_bytes(src.read_bytes())
        else:
            ff = find_ffmpeg()
            rr = subprocess.run(
                [
                    ff,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(src),
                    str(wav),
                ],
                capture_output=True,
                text=True,
            )
            if rr.returncode:
                raise RuntimeError("ffmpeg wav: " + (rr.stderr or "")[-800:])
    wav_to_mp3(wav, mp3)

    meta = {
        "model": "ACE-Step/ACE-Step-v1-3.5B",
        "prompt": prompt,
        "seconds": seconds,
        "seed": seed,
        "license": "generated with ACE-Step, Apache-2.0",
        "source_file": str(src.name),
    }
    (OUT / "music_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("done:", wav, mp3, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        print(err, flush=True)
        (OUT / "error.txt").write_text(err, encoding="utf-8")
        raise
'''


def _run(cmd: list[str], *, check: bool = True) -> None:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd)
    if check and r.returncode:
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(cmd)}")


def setup_ace_venv() -> None:
    print("pip install uv…", flush=True)
    _run([sys.executable, "-m", "pip", "install", "-q", "uv"])
    # imageio-ffmpeg on host for find_ffmpeg fallback if needed by outer shell
    _run([sys.executable, "-m", "pip", "install", "-q", "imageio-ffmpeg"], check=False)

    print("uv venv -p 3.11", ACE_VENV, flush=True)
    _run(["uv", "venv", "-p", "3.11", str(ACE_VENV)])

    py = str(ACE_PY)
    print("install torch+torchaudio (cu121)…", flush=True)
    _run(
        [
            "uv",
            "pip",
            "install",
            "-p",
            py,
            "torch",
            "torchaudio",
            "--index-url",
            "https://download.pytorch.org/whl/cu121",
        ]
    )
    print("install ACE-Step + imageio-ffmpeg…", flush=True)
    _run(
        [
            "uv",
            "pip",
            "install",
            "-p",
            py,
            "git+https://github.com/ace-step/ACE-Step.git",
            "imageio-ffmpeg",
        ]
    )
    # torchaudio >= 2.9 зберігає wav лише через torchcodec — закріплюємо 2.5.1 (cu121) + soundfile
    print("pin torch/torchaudio 2.5.1 + soundfile…", flush=True)
    _run(["uv", "pip", "install", "-p", py, "--reinstall-package", "torch", "--reinstall-package", "torchaudio", "--reinstall-package", "torchvision",
          "torch==2.5.1", "torchaudio==2.5.1", "torchvision==0.20.1", "--index-url", "https://download.pytorch.org/whl/cu121"])
    _run(["uv", "pip", "install", "-p", py, "soundfile"])


def run_generation() -> None:
    import threading

    GEN_SCRIPT.write_text(GEN_ACE_SRC, encoding="utf-8")
    print("running", ACE_PY, GEN_SCRIPT, flush=True)
    proc = subprocess.Popen(
        [str(ACE_PY), str(GEN_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None and proc.stderr is not None
    err_chunks: list[str] = []

    def _pump_stderr() -> None:
        for line in proc.stderr:
            err_chunks.append(line)
            sys.stdout.write(line)
            sys.stdout.flush()

    t = threading.Thread(target=_pump_stderr, daemon=True)
    t.start()
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    t.join()
    rc = proc.wait()
    if rc:
        err = "".join(err_chunks) or f"gen_ace.py exited {rc}"
        (OUT / "error.txt").write_text(err, encoding="utf-8")
        raise RuntimeError(f"gen_ace.py failed with code {rc}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("host python", sys.version, flush=True)
    try:
        setup_ace_venv()
        run_generation()
    except Exception:
        err = traceback.format_exc()
        print(err, flush=True)
        err_path = OUT / "error.txt"
        if not err_path.is_file() or not err_path.stat().st_size:
            err_path.write_text(err, encoding="utf-8")
        raise


if __name__ == "__main__":
    main()

"""Build work/render_bundle/ for kernel_render (shots, audio, clips, build2, qa).

  py -3.14 make_render_bundle.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

W = Path(__file__).resolve().parent
BUNDLE = W / "work" / "render_bundle"


def main() -> None:
    shots_p = W / "shots.json"
    clips_p = W / "clips.json"
    if not shots_p.is_file():
        sys.exit("missing shots.json")
    if not clips_p.is_file():
        sys.exit("missing clips.json — needed for render bundle")

    cfg = json.loads(shots_p.read_text(encoding="utf-8"))
    clips = json.loads(clips_p.read_text(encoding="utf-8"))

    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    BUNDLE.mkdir(parents=True)

    shutil.copy2(shots_p, BUNDLE / "shots.json")
    lex = W / "lexicon.json"
    if lex.is_file():
        shutil.copy2(lex, BUNDLE / "lexicon.json")
    shutil.copy2(W / "build2.py", BUNDLE / "build2.py")
    shutil.copy2(W / "qa.py", BUNDLE / "qa.py")

    # audio/
    audio_dst = BUNDLE / "audio"
    audio_dst.mkdir()
    audio_src = W / "audio"
    for s in cfg.get("shots", []):
        sid = s["id"]
        for ext in (".mp3", ".json"):
            src = audio_src / f"{sid}{ext}"
            if not src.is_file():
                sys.exit(f"missing {src}")
            shutil.copy2(src, audio_dst / src.name)

    # music/ if configured
    music = cfg.get("music")
    if music:
        music_dst = BUNDLE / "music"
        music_dst.mkdir()
        src = Path(music)
        if not src.is_file():
            src = W / music
        if not src.is_file():
            sys.exit(f"music not found: {music}")
        dest_name = src.name if src.suffix.lower() in (".mp3", ".wav") else (src.stem + ".mp3")
        dest = music_dst / dest_name
        shutil.copy2(src, dest)
        cfg2 = json.loads((BUNDLE / "shots.json").read_text(encoding="utf-8"))
        cfg2["music"] = f"music/{dest.name}"
        (BUNDLE / "shots.json").write_text(
            json.dumps(cfg2, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # clips/ + rewritten clips.json
    clips_dir = BUNDLE / "clips"
    clips_dir.mkdir()
    new_clips: dict = {}
    n = 0
    for sid, segs in clips.items():
        new_segs = []
        for i, seg in enumerate(segs):
            src = Path(seg["file"])
            if not src.is_file():
                src = W / seg["file"]
            if not src.is_file():
                sys.exit(f"clip missing: {seg.get('file')}")
            ext = src.suffix or ".mp4"
            name = f"{sid}_{i:02d}{ext}"
            shutil.copy2(src, clips_dir / name)
            entry = dict(seg)
            entry["file"] = f"clips/{name}"
            new_segs.append(entry)
            n += 1
        new_clips[sid] = new_segs
    (BUNDLE / "clips.json").write_text(
        json.dumps(new_clips, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # optional fonts/
    fonts_src = W / "fonts"
    if fonts_src.is_dir() and any(fonts_src.iterdir()):
        shutil.copytree(fonts_src, BUNDLE / "fonts")

    print(f"bundle: {BUNDLE} ({n} clips)", flush=True)


if __name__ == "__main__":
    main()

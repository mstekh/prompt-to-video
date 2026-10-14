"""Kaggle GPU kernel: score money-segments thumbs with Qwen2.5-VL-3B."""
import json, re, subprocess, sys
from pathlib import Path

IN = next((p.parent for p in Path("/kaggle/input").rglob("segments.json")), Path("/kaggle/input/money-segments"))  # шлях монтування в Kaggle буває різний
OUT = Path("/kaggle/working")
MODEL_ID = "Qwen/Qwen2.5-VL-3B-Instruct"
ZERO = {
    "match": 0, "quality": 0, "text_or_watermark": True,
    "faces_closeup": False, "motion_ok": False, "desc": "parse_fail",
}


def ensure_deps():
    try:
        import qwen_vl_utils  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "qwen-vl-utils"])


def parse_json(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{[^{}]*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return None


def normalize(obj):
    if not isinstance(obj, dict):
        return dict(ZERO)
    def num(k, lo=0, hi=10):
        try:
            v = float(obj.get(k, 0))
        except Exception:
            v = 0
        return int(max(lo, min(hi, round(v))))
    def flag(k, default=False):
        v = obj.get(k, default)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "y")
        return bool(v)
    desc = str(obj.get("desc", ""))[:80].strip() or "none"
    words = desc.split()
    if len(words) > 12:
        desc = " ".join(words[:12])
    return {
        "match": num("match"),
        "quality": num("quality"),
        "text_or_watermark": flag("text_or_watermark", True),
        "faces_closeup": flag("faces_closeup", False),
        "motion_ok": flag("motion_ok", False),
        "desc": desc,
    }


def main():
    ensure_deps()
    import torch
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration
    from qwen_vl_utils import process_vision_info

    segs = json.loads((IN / "segments.json").read_text(encoding="utf-8"))
    subjects = json.loads((IN / "shots_subjects.json").read_text(encoding="utf-8"))
    thumbs_root = IN / "thumbs"

    print("loading", MODEL_ID, flush=True)
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        MODEL_ID, torch_dtype=torch.float16, device_map="auto"
    )
    processor = AutoProcessor.from_pretrained(MODEL_ID)

    reviews = {}
    n = len(segs)
    for i, seg in enumerate(segs, 1):
        sid = seg["seg"]
        shot = seg.get("shot", sid.split("/")[0])
        subject = subjects.get(shot, subjects.get(shot.upper(), "relevant stock footage"))
        paths = []
        for rel in seg.get("thumbs", []):
            p = thumbs_root / rel
            if p.exists():
                paths.append(str(p))
        if len(paths) < 3:
            reviews[sid] = dict(ZERO)
            reviews[sid]["desc"] = "missing_thumbs"
            print(f"[{i}/{n}] {sid} missing thumbs", flush=True)
            continue
        prompt = (
            f"Shot subject: {subject}\n"
            "These 3 frames are from one 3-second video segment (start / mid / end).\n"
            "Reply with ONLY JSON (no markdown):\n"
            '{"match":0-10,"quality":0-10,"text_or_watermark":bool,'
            '"faces_closeup":bool,"motion_ok":bool,"desc":"<=12 words"}\n'
            "match = how well the segment fits the shot subject; "
            "quality = clarity/composition; "
            "text_or_watermark = any captions/logos/watermarks/subtitles; "
            "faces_closeup = tight face close-up; "
            "motion_ok = no obvious glitches or hard cuts inside the segment."
        )
        messages = [{
            "role": "user",
            "content": (
                [{"type": "image", "image": p} for p in paths]
                + [{"type": "text", "text": prompt}]
            ),
        }]
        try:
            text = processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            image_inputs, video_inputs = process_vision_info(messages)
            inputs = processor(
                text=[text], images=image_inputs, videos=video_inputs,
                padding=True, return_tensors="pt",
            )
            inputs = inputs.to(model.device)
            with torch.inference_mode():
                out_ids = model.generate(**inputs, max_new_tokens=160)
            trimmed = [o[len(inp):] for inp, o in zip(inputs.input_ids, out_ids)]
            raw = processor.batch_decode(
                trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )[0]
            parsed = parse_json(raw)
            reviews[sid] = normalize(parsed) if parsed is not None else dict(ZERO)
        except Exception as e:
            reviews[sid] = dict(ZERO)
            reviews[sid]["desc"] = f"err:{type(e).__name__}"
            print(f"[{i}/{n}] {sid} ERROR {e}", flush=True)
            continue
        print(
            f"[{i}/{n}] {sid} match={reviews[sid]['match']} "
            f"q={reviews[sid]['quality']} wm={reviews[sid]['text_or_watermark']} "
            f"mot={reviews[sid]['motion_ok']}",
            flush=True,
        )

    out_path = OUT / "reviews.json"
    out_path.write_text(json.dumps(reviews, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out_path} ({len(reviews)} reviews)", flush=True)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        print(tb, flush=True)
        Path("/kaggle/working/error.txt").write_text(tb, encoding="utf-8")
        raise

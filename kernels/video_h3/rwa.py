"""
Надсилає workflow-i2v-h3.json у ComfyUI через API (POST /prompt), минаючи
drag-and-drop у браузері.

Використовує GET /object_info, щоб коректно зіставити widgets_values кожної
ноди з іменами її вхідних полів (порядок віджетів у форматі графа UI не
збігається напряму з форматом API-промпту).

Копія lab/run-workflow-api.py (ROOT = тека, де лежить цей файл — після копіювання
в /tmp/lab на Kaggle працює так само, як локально). Відмінність від оригіналу:
HTTP 400 від /prompt раніше губив тіло відповіді (node_errors), і RunError казав
лише "HTTP Error 400" — на Kaggle без браузера це не діагностувати (рев'ю #72/#77).
"""
import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

HOST = "http://127.0.0.1:8188"
ROOT = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(ROOT, "workflow-i2v-h3.json")
COMFY_INPUT = os.path.join(ROOT, "ComfyUI", "input")


class RunError(Exception):
    pass


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("prompt_file", nargs="?", default="prompt-v3-reverse.txt")
    p.add_argument("start_frame", nargs="?", default="stage-100-final-v2.jpg")
    p.add_argument("--seconds", type=float, default=2.5,
                   help="тривалість; 2.5 -> 73 кадри, 5.0 -> 124")
    p.add_argument("--sparsity", type=float, default=0.95)
    p.add_argument("--steps", type=int, default=4)
    p.add_argument("--dense-last-steps", type=int, default=0)
    p.add_argument("--width", type=int, default=768)
    p.add_argument("--height", type=int, default=1344)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--dry-run", action="store_true",
                   help="зібрати граф і показати підстановки, нічого не надсилати")
    p.add_argument("--input-name", default=None,
                   help="ім'я кадру в ComfyUI/input (за замовчуванням — ім'я файлу)")
    p.add_argument("--timeout-min", type=float, default=180)
    return p.parse_args(argv)


def frames_for(seconds):
    # та сама формула, що в ноді 132 графа
    n = max(5, round(seconds * 24))
    return n + (5 - n % 17) % 17


def api_get(path):
    with urllib.request.urlopen(HOST + path, timeout=30) as r:
        return json.loads(r.read())


def api_post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(HOST + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # ComfyUI кладе причину відмови (node_errors) саме в тіло 400 — без нього
        # лишається голе "HTTP Error 400" і немає як зрозуміти, що зламалось
        body = e.read().decode("utf-8", errors="replace")[:2000]
        raise RunError(f"HTTP {e.code} від {path}: {body}") from e


def wait_for_server(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            api_get("/system_stats")
            return True
        except Exception:
            time.sleep(2)
    return False


def queued_ids():
    q = api_get("/queue")
    return {item[1] for item in q.get("queue_running", []) + q.get("queue_pending", [])}


def wait_queue_empty(timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        q = api_get("/queue")
        if not q.get("queue_running") and not q.get("queue_pending"):
            return True
        time.sleep(5)
    return False


def build_api_prompt(ui_graph, object_info):
    """UI-граф (nodes/links/widgets_values) -> API-граф (node_id: {class_type, inputs})."""
    link_src = {link[0]: (link[1], link[2]) for link in ui_graph["links"]}

    api = {}
    for node in ui_graph["nodes"]:
        node_id = str(node["id"])
        class_type = node["type"]
        info = object_info.get(class_type)
        if info is None:
            raise RuntimeError(f"Немає у object_info: {class_type} (нода {node_id}). "
                               f"Встанови відповідний custom node пакет.")

        inputs = {}

        # Сокети з "inputs" UI-ноди — не віджети, навіть непід'єднані (link: null);
        # непід'єднаний опційний сокет просто не передається.
        socket_names = set()
        for inp in node.get("inputs", []):
            socket_names.add(inp["name"])
            link_id = inp.get("link")
            if link_id is not None and link_id in link_src:
                src_id, src_slot = link_src[link_id]
                inputs[inp["name"]] = [str(src_id), src_slot]

        req = info.get("input", {}).get("required", {})
        opt = info.get("input", {}).get("optional", {})
        all_input_specs = list(req.items()) + list(opt.items())
        widget_names_in_order = [name for name, spec in all_input_specs if name not in socket_names]

        for name, value in zip(widget_names_in_order, node.get("widgets_values", [])):
            inputs[name] = value

        api[node_id] = {"class_type": class_type, "inputs": inputs}

    return api


def apply_overrides(ui_graph, api_graph, args, prompt_text):
    for node in ui_graph["nodes"]:
        nid = str(node["id"])
        t = node["type"]
        inp = api_graph[nid]["inputs"]
        if t == "PrimitiveStringMultiline":
            inp["value"] = prompt_text
        elif t == "LoadImage" and node["id"] == 901:
            inp["image"] = args.input_name or os.path.basename(args.start_frame)
        elif t == "MiniMaxH3ImageToVideo":
            # 768x1344 не є точним виходом ResolutionSelector, тож задаємо пікселі напряму
            inp["width"] = args.width
            inp["height"] = args.height
        elif t == "MiniMaxChunkFeedForward":
            inp["chunks"] = 16
        elif t == "PrimitiveFloat" and node["id"] == 133:
            inp["value"] = args.seconds
        elif t == "RandomNoise":
            inp["noise_seed"] = args.seed
        elif t == "BasicScheduler":
            inp["steps"] = args.steps
        elif t == "H3SLAAttention":
            # min_seq_len=8192 з графа > нашої послідовності -> attention йшла dense і
            # падала в OOM на 4 ГБ. 0 форсує sparse-шлях завжди.
            inp["min_seq_len"] = 0
            inp["sparsity_ratio"] = args.sparsity
            inp["dense_last_steps"] = args.dense_last_steps


def run(args):
    """Повертає outputs ComfyUI (dict) або кидає RunError; при dry_run повертає None."""
    prompt_path = os.path.join(ROOT, args.prompt_file)
    frame_path = os.path.join(ROOT, args.start_frame)
    for path in (prompt_path, frame_path):
        if not os.path.exists(path):
            raise RunError(f"Немає файлу: {path}")

    if not wait_for_server():
        raise RunError("ComfyUI не відповідає на http://127.0.0.1:8188 — запусти run-comfyui.ps1 спершу.")

    os.makedirs(COMFY_INPUT, exist_ok=True)
    shutil.copyfile(frame_path, os.path.join(COMFY_INPUT, args.input_name or os.path.basename(frame_path)))

    with open(WORKFLOW, encoding="utf-8") as f:
        ui_graph = json.load(f)
    with open(prompt_path, encoding="utf-8") as f:
        prompt_text = f.read()

    api_graph = build_api_prompt(ui_graph, api_get("/object_info"))
    apply_overrides(ui_graph, api_graph, args, prompt_text)

    print(f"prompt={args.prompt_file} start={args.start_frame} {args.width}x{args.height} "
          f"{args.seconds}s={frames_for(args.seconds)} frames steps={args.steps} "
          f"sparsity={args.sparsity} dense_last_steps={args.dense_last_steps} seed={args.seed}",
          flush=True)

    if args.dry_run:
        for nid, node in api_graph.items():
            if node["class_type"] in ("BasicScheduler", "RandomNoise", "H3SLAAttention",
                                      "MiniMaxH3ImageToVideo", "PrimitiveFloat", "LoadImage"):
                shown = {k: v for k, v in node["inputs"].items() if not isinstance(v, list)}
                print(f"  {nid} {node['class_type']}: {shown}")
        return None

    # після OOM або /interrupt попереднє виконання може ще прибирати за собою
    try:
        queue_free = wait_queue_empty(timeout=600)
    except Exception as e:
        raise RunError(f"не вдалося прочитати чергу ComfyUI: {e}")
    if not queue_free:
        raise RunError("черга ComfyUI не звільнилась за 10 хв")

    result = api_post("/prompt", {"prompt": api_graph, "client_id": "run-workflow-api"})
    prompt_id = result.get("prompt_id")
    if not prompt_id:
        raise RunError(f"ComfyUI відхилив prompt: {json.dumps(result, ensure_ascii=False)}")

    print(f"Прийнято, prompt_id = {prompt_id}. Очікую завершення...", flush=True)

    deadline = time.time() + args.timeout_min * 60
    last_ok = time.time()
    missing = 0
    while time.time() < deadline:
        try:
            # спершу черга, потім історія: завдання, що завершилось між запитами, потрапить в історію
            in_queue = prompt_id in queued_ids()
            hist = api_get(f"/history/{prompt_id}")
            last_ok = time.time()
        except Exception as e:
            if time.time() - last_ok > 180:
                raise RunError(f"сервер недоступний понад 3 хв: {e}")
            time.sleep(10)
            continue
        if prompt_id not in hist and not in_queue:
            # сервер перезапустився й забув завдання
            missing += 1
            if missing >= 3:
                raise RunError("завдання зникло з сервера (ймовірно, перезапуск)")
        else:
            missing = 0
        if prompt_id in hist:
            entry = hist[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                msg = "невідома помилка"
                for msg_type, msg_data in status.get("messages", []):
                    if msg_type == "execution_error":
                        msg = (f"нода {msg_data.get('node_id')} ({msg_data.get('node_type')}): "
                               f"{msg_data.get('exception_message', '').strip()}")
                raise RunError(msg)
            return entry.get("outputs", {})
        time.sleep(5)

    api_post("/interrupt", {})
    raise RunError(f"таймаут {args.timeout_min} хв, виконання перервано")


def main():
    try:
        outputs = run(parse_args())
    except RunError as e:
        sys.exit(f"ПОМИЛКА: {e}")
    if outputs is not None:
        print("Готово. Outputs:")
        print(json.dumps(outputs, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

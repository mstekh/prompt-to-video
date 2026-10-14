"""
Одна відео-задача на Kaggle (MiniMax H3, image-to-video).

Самодостатній скрипт без залежності від genrunner — усе потрібне приїжджає в one-shot
датасеті (job.json, input.<ext>, workflow-i2v-h3.json, rwa.py, manifest.json; власник
дозволяє лише одну задачу за запуск, без пакетів). Піни ComfyUI/нод/torch і сам граф
ComfyUI-запиту — з lab/kaggle/h3_batch.py, перевірено в бою, НЕ чіпати.

BATCH_ID і MANIFEST_SHA підставляє genrunner.kaggle_exec при push; звірка з manifest.json
йде до setup(), щоб застарілий чи чужий датасет не з'їв 15+ хв на завантаження моделей
даремно (рев'ю #40, #44). Статус завжди лише в results.json (пишеться в finally) — вихід
з кодом 0 незалежно від результату, "COMPLETE" від Kaggle саме по собі нічого не означає
(runner.py довіряє лише results.json; ядро гасить себе сторожем ще до того, як runner
перестане чекати).
"""
import glob
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback

BATCH_ID = "__BATCH_ID__"
MANIFEST_SHA = "__MANIFEST_SHA__"

COMFY_SHA = "b5cc8830279eae909a59de030af1e50761c36751"
NODES = [
    ("https://github.com/PlagueKind/ComfyUI-PlagueKind-Nodes", "d58d006a4ea32c25c06499f2ff104f0852a045a6"),
    ("https://github.com/kijai/ComfyUI-KJNodes", "d3cfe21625e5170126ce06fbfcfe1d88108688c3"),
    ("https://github.com/evanspearman/ComfyMath", "c01177221c31b8e5fbc062778fc8254aeb541638"),
]
MODELS = [
    ("MATLOWAI/minimax-h3-fused-turbo-int8-convrot",
     "diffusion_models/minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors"),
    ("Comfy-Org/MiniMax-H3", "vae/minimax_h3_video_vae_int8_convrot.safetensors"),
    ("Comfy-Org/MiniMax-H3", "vae/minimax_h3_audio_vae_fp32.safetensors"),
    ("Comfy-Org/MiniMax-H3", "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"),
]
RESOLUTIONS = {"portrait": (768, 1344), "landscape": (1344, 768)}
STEPS, SPARSITY = 4, 0.95
BUDGET_MIN = 80  # запас під час опитування genrunner (timeout_video_min типово 90)

T0 = time.time()
# GS_KERNEL_OUT/GS_KERNEL_INPUT дають tests/test_kernels.py підмінити /kaggle/* на теку в
# tmp — на самому Kaggle ці змінні не задані, і нічого не міняється
OUT = os.environ.get("GS_KERNEL_OUT", "/kaggle/working/out")
KAGGLE_INPUT = os.environ.get("GS_KERNEL_INPUT", "/kaggle/input")
LAB = "/tmp/lab"
COMFY = f"{LAB}/ComfyUI"
os.makedirs(OUT, exist_ok=True)
_log = open(f"{OUT}/run.log", "a", encoding="utf-8")


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} +{(time.time() - T0) / 60:5.1f}m  {msg}"
    print(line, flush=True)
    _log.write(line + "\n")
    _log.flush()


def sh(cmd, cwd=None, timeout=300, retries=0, env=None):
    """Команда з таймаутом і повторами — без цього одне зависання git/pip з'їдає всю
    сесію Kaggle (рев'ю #8, #55). start_new_session=True кладе shell у власну групу
    процесів; на таймауті вбиваємо ЦІЛУ групу через os.killpg (SIGKILL), а не лише
    proc.pid — інакше git/pip, яких shell устиг запустити, лишаються сиротами й далі
    пишуть у ту саму теку паралельно з повторною спробою (рев'ю #10). os.killpg є лише
    на POSIX — на Kaggle це завжди так; на іншій ОС просто вбиваємо сам процес."""
    last_err = "?"
    for i in range(retries + 1):
        tag = f" (спроба {i + 1}/{retries + 1})" if i else ""
        log(f"$ {cmd[:300]}{tag}")
        proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    # R2-4 (Gemini 2): os.getpgid(proc.pid) САМ кидає ProcessLookupError,
                    # якщо лідер групи вже помер (зомбі ще не зачищений, або процес устиг
                    # завершитись сам до вбивства) — тоді виняток стається ДО killpg, і вся
                    # решта групи (діти shell, яких і треба вбити) лишається живою. pid ==
                    # pgid тут гарантовано завжди (start_new_session=True робить group leader
                    # = сам proc.pid), тож getpgid зайвий — б'ємо групу напряму через pid;
                    # ProcessLookupError лишається на випадок, якщо вся група вже мертва.
                    # signal.SIGKILL немає в модулі на Windows — цей код усе одно виконується
                    # лише на Kaggle (Linux); getattr лише страхує від AttributeError, якщо
                    # os.name підмінено штучно (тести на цій Windows-машині розробника).
                    os.killpg(proc.pid, getattr(signal, "SIGKILL", 9))
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            try:
                out, err = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                out, err = "", ""
            last_err = f"таймаут {timeout}с"
            log(f"  {last_err}")
            continue
        if proc.returncode == 0:
            return out
        last_err = f"код {proc.returncode}"
        log((out[-1500:] + "\n" + err[-3000:]).strip())
    raise RuntimeError(f"команда впала ({last_err}): {cmd}")


def git_at(url, sha, dest):
    os.makedirs(dest, exist_ok=True)
    sh("git init -q && git remote add origin " + url, cwd=dest, timeout=60)
    sh(f"git fetch -q --depth 1 origin {sha} && git checkout -q FETCH_HEAD", cwd=dest, timeout=300, retries=2)


def mem_report(tag):
    gpu = subprocess.run("nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader",
                         shell=True, text=True, capture_output=True).stdout.strip().replace("\n", " | ")
    ram = subprocess.run("free -g | awk '/Mem:/ {print $3\"/\"$2\" GB used\"}'",
                         shell=True, text=True, capture_output=True).stdout.strip()
    log(f"[{tag}] GPU {gpu} | RAM {ram}")


def verify_manifest(src_dir):
    """Manifest.json звіряється з BATCH_ID/MANIFEST_SHA, вшитими в код push-ом, і з sha256
    кожного файлу входу. Розбіжність = застарілий чи чужий датасет -> stale_dataset до
    будь-якого завантаження моделей (рев'ю #40, #44). Винесено окремою функцією, щоб тести
    викликали її напряму без GPU (GS_KERNEL_TEST=1 вимикає авто-запуск main())."""
    manifest_path = os.path.join(src_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return False, None, "немає manifest.json у вході"
    raw = open(manifest_path, "rb").read()
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != MANIFEST_SHA:
        return False, None, f"sha manifest.json не збігся: {actual_sha} != {MANIFEST_SHA}"
    manifest = json.loads(raw)
    if manifest.get("batch_id") != BATCH_ID:
        return False, manifest, f"batch_id не збігся: {manifest.get('batch_id')} != {BATCH_ID}"
    for name, expected in manifest.get("files", {}).items():
        p = os.path.join(src_dir, name)
        if not os.path.exists(p):
            return False, manifest, f"немає файлу з manifest: {name}"
        got = hashlib.sha256(open(p, "rb").read()).hexdigest()
        if got != expected:
            return False, manifest, f"sha файлу {name} не збігся: {name}"
    return True, manifest, ""


def server_tail(n=60):
    try:
        with open(f"{OUT}/comfyui.log", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError as e:
        return f"(лог не прочитати: {e})"


def setup():
    sh("pip install -q torch==2.14.0 torchvision==0.29.0 torchaudio==2.11.0 "
       "--index-url https://download.pytorch.org/whl/cu130", timeout=1200, retries=2)
    git_at("https://github.com/comfyanonymous/ComfyUI", COMFY_SHA, COMFY)
    sh(f"pip install -q -r {COMFY}/requirements.txt", timeout=1200, retries=2)
    for url, sha in NODES:
        dest = f"{COMFY}/custom_nodes/{url.rsplit('/', 1)[1]}"
        git_at(url, sha, dest)
        if os.path.exists(f"{dest}/requirements.txt"):
            sh(f"pip install -q -r {dest}/requirements.txt", timeout=1200, retries=2)
    sh('python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"',
       timeout=60)

    # Xet качає в рази швидше, але спільний huggingface_hub ламав старт ComfyUI (перевірено
    # раніше), тож завантажувач живе в окремій теці й не чіпає site-packages ComfyUI
    sh('pip install -q --target /tmp/hfdl "huggingface_hub[hf_xet]"', timeout=600, retries=2)
    dl = (
        "import os, sys, time\n"
        "from concurrent.futures import ThreadPoolExecutor\n"
        "from huggingface_hub import hf_hub_download\n"
        "jobs = [a.split('::') for a in sys.argv[2:]]\n"
        "def fetch(j):\n"
        "    t = time.time(); p = hf_hub_download(repo_id=j[0], filename=j[1], local_dir=sys.argv[1])\n"
        "    print(f'модель {os.path.basename(p)}: {os.path.getsize(p)/2**30:.1f} ГБ за {time.time()-t:.0f} с', flush=True)\n"
        "with ThreadPoolExecutor(len(jobs)) as pool: list(pool.map(fetch, jobs))\n"
    )
    with open("/tmp/hfdl_run.py", "w", encoding="utf-8") as f:
        f.write(dl)
    env = dict(os.environ, PYTHONPATH="/tmp/hfdl", HF_XET_HIGH_PERFORMANCE="1")
    args = " ".join(f"'{repo}::{path}'" for repo, path in MODELS)
    sh(f"python /tmp/hfdl_run.py {COMFY}/models {args}", timeout=2700, retries=1, env=env)


def start_server():
    env = dict(os.environ, PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True", CUDA_VISIBLE_DEVICES="0",
               PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, "main.py", "--listen", "127.0.0.1", "--port", "8188",
                             "--preview-method", "none"], cwd=COMFY, env=env,
                            stdout=open(f"{OUT}/comfyui.log", "a", encoding="utf-8"), stderr=subprocess.STDOUT)
    return proc


def wait_up(proc, rwa_mod):
    # опитуємо /system_stats і proc.poll() на кожному кроці — мертвий процес не чекаємо
    # сліпо до кінця фіксованого таймауту (рев'ю #58)
    for _ in range(120):
        if rwa_mod.wait_for_server(timeout=2):
            return
        if proc.poll() is not None:
            raise RuntimeError(f"ComfyUI завершився з кодом {proc.poll()}; хвіст:\n{server_tail()}")
        time.sleep(5)
    raise RuntimeError(f"ComfyUI не піднявся за 10 хв; хвіст:\n{server_tail()}")


def _watchdog(budget_min, results, out_path, timer_box):
    def fire():
        log(f"СТОРОЖ: бюджет {budget_min} хв вийшов — пишу timeout і виходжу")
        results["status"] = "timeout"
        results["minutes_total"] = round((time.time() - T0) / 60, 1)
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
        finally:
            os._exit(0)  # головний потік міг зависнути в мережевому виклику без свого timeout

    t = threading.Timer(budget_min * 60, fire)
    t.daemon = True
    t.start()
    timer_box.append(t)
    return t


def main():
    results = {"batch_id": BATCH_ID, "job_id": None, "status": "failed", "error": None,
               "minutes_total": 0.0, "files": []}
    results_path = f"{OUT}/results.json"
    timer_box = []
    try:
        src = os.path.dirname(glob.glob(f"{KAGGLE_INPUT}/**/job.json", recursive=True)[0])
        job = json.load(open(f"{src}/job.json", encoding="utf-8"))
        results["job_id"] = job["job_id"]
        log(f"завдання: {job}")

        ok, manifest, reason = verify_manifest(src)
        if not ok:
            log(f"manifest не збігся: {reason}")
            results["status"] = "stale_dataset"
            results["error"] = reason
            return  # до setup — не тягнемо моделі під застарілий/чужий вхід

        _watchdog(BUDGET_MIN, results, results_path, timer_box)

        input_files = [f for f in os.listdir(src) if f.startswith("input.")]
        if not input_files:
            raise RuntimeError("у вході немає input.<ext>")
        input_name = input_files[0]

        os.makedirs(LAB, exist_ok=True)
        for name in ("workflow-i2v-h3.json", "rwa.py"):
            shutil.copy(f"{src}/{name}", f"{LAB}/{name}")
        shutil.copy(f"{src}/{input_name}", f"{LAB}/{input_name}")

        params = job.get("params", {})
        prompt_text = params.get("prompt", "")
        if not prompt_text:
            raise RuntimeError("job.json: немає prompt для відео")
        open(f"{LAB}/prompt.txt", "w", encoding="utf-8").write(prompt_text)

        setup()
        mem_report("після setup")

        sys.path.insert(0, LAB)
        import importlib.util
        spec = importlib.util.spec_from_file_location("rwa", f"{LAB}/rwa.py")
        rwa = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rwa)

        server = start_server()
        wait_up(server, rwa)
        log("ComfyUI піднявся")

        seconds = float(params.get("seconds", 5.0))
        width, height = RESOLUTIONS[params.get("orientation", "portrait")]
        seed = int(params.get("seed", 42))
        elapsed_min = (time.time() - T0) / 60
        gen_timeout_min = max(5, min(180, BUDGET_MIN - elapsed_min - 5))

        args = rwa.parse_args([
            "prompt.txt", input_name,
            "--seconds", str(seconds), "--width", str(width), "--height", str(height),
            "--steps", str(STEPS), "--sparsity", str(SPARSITY), "--seed", str(seed),
            "--input-name", input_name, "--timeout-min", str(gen_timeout_min)])
        log(f"старт ({seconds} с = {rwa.frames_for(seconds)} кадрів, {width}x{height})")

        t = time.time()
        outputs = rwa.run(args)
        video = outputs["92"]["images"][0]
        src_video = f"{COMFY}/output/{video.get('subfolder', '')}/{video['filename']}"
        dst_dir = f"{OUT}/{job['job_id']}"
        os.makedirs(dst_dir, exist_ok=True)
        dst_video = f"{dst_dir}/video.mp4"
        shutil.copyfile(src_video, dst_video)
        minutes = (time.time() - t) / 60
        log(f"готово за {minutes:.1f} хв -> {dst_video}")

        results["status"] = "done"
        results["files"] = ["video.mp4"]
    except Exception:
        log("ФАТАЛЬНА ПОМИЛКА:\n" + traceback.format_exc())
        results["error"] = traceback.format_exc(limit=3)[-500:]
    finally:
        if timer_box:
            timer_box[0].cancel()
        results["minutes_total"] = round((time.time() - T0) / 60, 1)
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        log(f"кінець, усього {results['minutes_total']:.1f} хв, статус {results['status']}")


if not os.environ.get("GS_KERNEL_TEST"):
    main()

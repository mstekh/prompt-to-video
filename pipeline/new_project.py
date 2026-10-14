"""Створює теку проєкту для одного відео: копіює скрипти, шрифти, ядра Kaggle й приклади.

  py pipeline/new_project.py projects/my-video
  cd projects/my-video          # усі команди з документації виконуються звідси
Скрипти читають shots.json, lexicon.json, audio/, work/ зі своєї теки, тому кожне відео живе у власній теці.
"""
import shutil, sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if len(sys.argv) < 2:
    sys.exit(__doc__)
dst = Path(sys.argv[1]).resolve()
dst.mkdir(parents=True, exist_ok=True)
for f in (root / "pipeline").iterdir():
    if f.name in ("new_project.py", "__pycache__"):
        continue
    if f.is_dir():
        shutil.copytree(f, dst / f.name, dirs_exist_ok=True)
    else:
        shutil.copy2(f, dst / f.name)
shutil.copytree(root / "kernels", dst / "kernels", dirs_exist_ok=True)
shutil.copytree(root / "examples", dst / "examples", dirs_exist_ok=True)
if not (dst / "shots.json").exists():
    shutil.copy2(root / "examples/money/shots.example.json", dst / "shots.json")
if not (dst / "lexicon.json").exists():
    shutil.copy2(dst / "lexicon.example.json", dst / "lexicon.json")
for d in ("audio", "music", "work", "out"):
    (dst / d).mkdir(exist_ok=True)
print(f"Проєкт створено: {dst}\nДалі:  cd \"{dst}\"  і виконуйте кроки з docs/03-step-by-step.md (shots.json — зразок, замініть своїм сценарієм).")

# Акаунти й встановлення (≈1 година, один раз)

**Claude Code, прочитай і виконуй по черзі; людину залучай лише там, де написано «👤».** Усе реєструється на людину, з її номером і без VPN.
Ключі людина вписує сама у змінні середовища або файли поза git, **ніколи в чат** і не в репозиторій.

## Два режими

| | Режим 0 (нуль реєстрацій) | Режим 1 (повний конвеєр, цей репозиторій) |
|---|---|---|
| Для кого | перша спроба, некомерційний канал | якісний ролик з унікальними AI-кліпами |
| Потрібно | Python, ffmpeg, `edge-tts`, numpy, Pillow | + Kaggle (GPU), далі за бажанням агенти й ключі |
| Футаж | лише Wikimedia Commons | AI-кліпи зі стартових кадрів Commons (основний) або стоки |
| Результат | ролик із нерухомих кадрів і анімації тексту | живі кліпи, музика ACE-Step, рендер на Kaggle |

## 👤 Акаунти

| Сервіс | Навіщо | Де | Обов'язково? |
|---|---|---|---|
| GitHub | клон, документи | github.com | так |
| Claude Code (підписка) | оркестратор | claude.ai | так |
| **Kaggle** | безкоштовний GPU 2×T4, ~30 год/тиждень | kaggle.com → Settings → **Phone verification** (інакше немає GPU й інтернету в ядрах) → API → Create New Token | так (режим 1) |
| Pixabay | стокові відео й музика (запасний футаж) | pixabay.com/api/docs → ключ у рядку `key (required)` | ні |
| NVIDIA Build | безкоштовна модель для агента Hermes (≈40 запитів/хв) | build.nvidia.com | ні |
| Cursor | сильні моделі для сценарію й фактчеку | cursor.com | ні (можна й сам Claude) |
| Telegram-бот | чернетки відео на телефон | @BotFather | ні |

Pexels API на жовтень 2026 не видавав нові ключі (видача призупинена); Gemini API може бути в ліміті витрат (429) — конвеєр без нього працює.

Змінні середовища (Windows, PowerShell; Linux — `export`):
```powershell
[Environment]::SetEnvironmentVariable('PIXABAY_API_KEY','…','User')
[Environment]::SetEnvironmentVariable('KAGGLE_USERNAME','ваш_логін','User')   # якщо не використовуєте kaggle.json
```
Kaggle-токен: файл `~/.kaggle/kaggle.json` (`{"username":"…","key":"…"}`) або `~/.kaggle/access_token` для нового формату CLI. Перевірка: `kaggle kernels list --mine`.

## Програми (Windows: winget, Linux/macOS: пакетний менеджер)
```
winget install -e --id Python.Python.3.12
winget install -e --id Git.Git
winget install -e --id GitHub.cli
py -m pip install --user -r pipeline/requirements.txt
```
ffmpeg окремо ставити не треба: його дає `imageio-ffmpeg` (`py -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"`).
Kaggle CLI 2.x потребує Python ≥ 3.11.

## Правила середовища
- **Локальних моделей (Ollama тощо) не запускати.** ПК лишається вільним; усе важке — на Kaggle й у хмарних сервісах.
- Довгі задачі запускати **окремо від термінала/сесії** — інакше вони гинуть при перезапуску сесії (Windows: `Win32_Process.Create`; Linux: `nohup` або `systemd-run --user`).
- Логін Kaggle ніде не вписувати в код: `KAGGLE_USERNAME`, `~/.kaggle/kaggle.json` або `kaggle config view` (клони чужих репозиторіїв із вписаним логіном не стартують: пуш в чужий простір імен заборонено).

## Агенти (необов'язково, економлять токени Claude)
- **Hermes** (безкоштовний агент, Windows нативно): інсталятор `https://hermes-agent.nousresearch.com/install.ps1` (спершу перегляньте файл), вхід у Nous Portal 👤,
  основна модель — NVIDIA NIM, запуск `hermes chat --query-file brief.txt --oneshot -Q --max-turns 60 --run-budget 1800 --in <тека>`.
  Повільний (крок 15–80 с, ліміт 180 с на команду) — давайте йому швидкі готові скрипти, не «візуальну оцінку».
- **Cursor** (`cursor-agent`/MCP): модель `auto` працює в межах підписки; режим `ask` без веб-пошуку.
- **OpenClaw** (WSL) — запасний агент; у цьому конвеєрі не обов'язковий.
Конфіденційне через безкоштовні тарифи не пускати: частина з них використовує запити для навчання.

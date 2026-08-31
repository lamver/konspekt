"""Проверка выпуска: скачать релиз обратно и сверить сумму.

Страница релиза может выглядеть целой, а файлов в ней не быть: `gh
release create` с файлами в конце команды молча их теряет. Поэтому верим
только тому, что скачалось с настоящего адреса.

Заодно смотрим latest.json: без него автообновление промолчит, сколько
релиз ни выпускай.

Запуск: .venv\\Scripts\\python.exe tools/проверить_выпуск.py
"""

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import testenv  # noqa: E402,F401  русский вывод в консоли Windows
from app import __version__  # noqa: E402

ТЕГ = f"v{__version__}"
ИМЯ = f"konspekt-{__version__}-setup.exe"
ОСНОВА = f"https://github.com/lamver/konspekt-releases/releases/download/{ТЕГ}"
LATEST = "https://raw.githubusercontent.com/lamver/konspekt-releases/master/latest.json"


def скачать(адрес: str) -> bytes:
    запрос = urllib.request.Request(адрес, headers={"User-Agent": "konspekt"})
    with urllib.request.urlopen(запрос, timeout=180) as ответ:
        return ответ.read()


def main() -> int:
    беда = False

    суммы = скачать(f"{ОСНОВА}/SHA256SUMS").decode("utf-8")
    print("SHA256SUMS:", суммы.strip())

    данные = скачать(f"{ОСНОВА}/{ИМЯ}")
    сумма = hashlib.sha256(данные).hexdigest()
    print(f"скачано {len(данные)} байт, сумма {сумма}")
    if сумма in суммы:
        print("[ok] сумма сходится: люди получат ровно эту сборку")
    else:
        print("[FAIL] сумма не сошлась, обновление откажется ставиться")
        беда = True

    сведения = json.loads(скачать(LATEST))
    print("latest.json отдаёт", сведения.get("version"))
    if сведения.get("version") == __version__:
        print("[ok] автообновление увидит новую версию")
    else:
        print("[FAIL] latest.json отстал, автообновление промолчит")
        беда = True

    return 1 if беда else 0


if __name__ == "__main__":
    raise SystemExit(main())

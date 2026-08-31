"""Обновить latest.json в публичном репозитории релизов.

Отдельным скриптом, потому что руками это делается через base64 и sha
существующего файла, и один промах оставляет пользователей без
обновления, ничего не сказав.

Поле notes показывается в окне программы, поэтому оно на русском:
это сообщение интерфейса, а не публичная страница релиза.
"""

import base64
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import testenv  # noqa: E402,F401  русский вывод в консоли Windows

from app import __version__  # noqa: E402

РЕПОЗИТОРИЙ = "lamver/konspekt-releases"
ФАЙЛ = "latest.json"


def гх(*аргументы: str) -> str:
    готово = subprocess.run(
        ["gh", *аргументы], capture_output=True, text=True, shell=True,
        encoding="utf-8", errors="replace",
    )
    if готово.returncode != 0:
        raise SystemExit(f"gh не смог: {готово.stderr.strip()}")
    return готово.stdout.strip()


def main() -> int:
    if len(sys.argv) < 2:
        print("Использование: обновить_latest.py \"краткая новость для окна\"")
        return 1
    новость = sys.argv[1]

    старый_sha = гх("api", f"repos/{РЕПОЗИТОРИЙ}/contents/{ФАЙЛ}", "--jq", ".sha")

    содержимое = {
        "version": __version__,
        "url": f"https://github.com/{РЕПОЗИТОРИЙ}/releases/latest",
        "notes": новость,
    }
    текст = json.dumps(содержимое, ensure_ascii=False, indent=2) + "\n"
    закодировано = base64.b64encode(текст.encode("utf-8")).decode("ascii")

    гх(
        "api", f"repos/{РЕПОЗИТОРИЙ}/contents/{ФАЙЛ}",
        "--method", "PUT",
        "-f", f"message=Версия {__version__}",
        "-f", f"content={закодировано}",
        "-f", f"sha={старый_sha}",
    )
    print(f"latest.json обновлён до {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

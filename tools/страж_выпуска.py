"""Страж выпуска: установщик с тревогой антивируса не уходит к людям.

Зачем. Сборка PyInstaller не воспроизводима до байта, и одни и те же
исходники получают у Microsoft Defender то 0, то 1-2 тревоги
(`Trojan:Win32/Wacatac.B!ml`, догадка машинного обучения по байтам).
0.10.1 так и ушла к людям с тревогой: скан шёл, но уже после публикации,
и результат только приписывался к описанию. У человека при этом Defender
удаляет установщик, не спрашивая.

Теперь скан идёт до публикации, и сборка с тревогой падает. Пересобрать
тот же код и получить чистый файл — обычное дело: проверено пятью
сборками подряд 29 сентября.

Запуск на сборочной машине:

    python tools/страж_выпуска.py dist/konspekt-0.11.0-setup.exe

Код выхода 0 — чисто, 1 — тревога (сборку пересобрать), 2 — проверить
не удалось (сеть, ключ). Ключ берётся из VT_API_KEY.

Разбор ответа вынесен в чистые функции и проверяется в страж_test.py
без сети: отладка здесь стоит сборки на десять минут.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://www.virustotal.com/api/v3"
ОЖИДАНИЕ = 15
ПОПЫТОК = 60

# Сколько тревог допускаем. Ноль: ради этого страж и стоит. Одна тревога
# от малоизвестного движка кажется мелочью, но именно Microsoft стоит у
# каждого, а человеку всё равно, какой движок удалил его установщик.
ДОПУСТИМО = 0


def вердикт(атрибуты: dict) -> tuple[int, int, list[str]]:
    """Сколько тревог, из скольки движков, и кто именно ругается.

    Подозрительные считаем вместе с вредоносными: движки часто пишут не
    «вирус», а «подозрительно», и Defender удаляет файл в обоих случаях.
    Движки, которые файл не открыли (type-unsupported, timeout), в число
    проверивших не входят: «0 из 75» не должно значить «0 из 6».
    """
    итог = атрибуты.get("last_analysis_stats") or {}
    тревог = int(итог.get("malicious", 0)) + int(итог.get("suspicious", 0))
    проверили = тревог + int(итог.get("undetected", 0)) + int(итог.get("harmless", 0))
    кто = sorted(
        f"{движок}: {вывод.get('result') or вывод.get('category')}"
        for движок, вывод in (атрибуты.get("last_analysis_results") or {}).items()
        if вывод.get("category") in ("malicious", "suspicious")
    )
    return тревог, проверили, кто


def годится(тревог: int, проверили: int) -> bool:
    """Можно ли выпускать. Без проверивших движков — нельзя: это не чисто, а неизвестно."""
    return проверили > 0 and тревог <= ДОПУСТИМО


def _запрос(адрес: str, ключ: str, данные: bytes | None = None,
            заголовки: dict | None = None) -> dict:
    запрос = urllib.request.Request(
        адрес, data=данные, headers={"x-apikey": ключ, **(заголовки or {})},
    )
    with urllib.request.urlopen(запрос, timeout=900) as ответ:
        return json.load(ответ)


def _залить(файл: Path, ключ: str) -> str:
    """Отправить файл, вернуть номер разбора.

    Файлы больше 32 МБ принимает только особый адрес, установщик весит 56.
    """
    адрес = _запрос(f"{API}/files/upload_url", ключ)["data"]
    граница = "----konspekt-guard"
    тело = (
        f"--{граница}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{файл.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + файл.read_bytes() + f"\r\n--{граница}--\r\n".encode()
    ответ = _запрос(адрес, ключ, тело,
                    {"content-type": f"multipart/form-data; boundary={граница}"})
    return ответ["data"]["id"]


def _дождаться(разбор: str, ключ: str) -> None:
    for попытка in range(ПОПЫТОК):
        time.sleep(ОЖИДАНИЕ)
        состояние = _запрос(f"{API}/analyses/{разбор}", ключ)["data"]["attributes"]["status"]
        print(f"разбор: {состояние} ({попытка + 1})", flush=True)
        if состояние == "completed":
            return
    raise TimeoutError("разбор не закончился за 15 минут")


def main(аргументы: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(аргументы) != 1:
        print(__doc__)
        return 2
    файл = Path(аргументы[0])
    ключ = os.environ.get("VT_API_KEY", "")
    if not ключ:
        print("нет VT_API_KEY: проверить установщик нечем, выпускать вслепую нельзя")
        return 2

    сумма = hashlib.sha256(файл.read_bytes()).hexdigest()
    print(f"{файл.name}: {сумма}")
    try:
        _дождаться(_залить(файл, ключ), ключ)
        атрибуты = _запрос(f"{API}/files/{сумма}", ключ)["data"]["attributes"]
    except (urllib.error.URLError, TimeoutError, KeyError) as беда:
        print(f"проверить не удалось: {беда}")
        return 2

    тревог, проверили, кто = вердикт(атрибуты)
    print(f"тревог: {тревог} из {проверили}")
    for строка in кто:
        print(f"  {строка}")
    print(f"отчёт: https://www.virustotal.com/gui/file/{сумма}")
    if годится(тревог, проверили):
        print("чисто, можно выпускать")
        return 0
    print("ТРЕВОГА: этот установщик к людям не пойдёт. Пересоберите тот же код:"
          " сборка не воспроизводима до байта, и тревога чаще всего пропадает.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

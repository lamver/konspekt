"""Прогнать через VirusTotal установщик, который уже лежит в релизе.

Обычная сборка сканирует свежесобранный файл, а у людей на руках тот,
что выложен: суммы у них разные, сборка не воспроизводима. Для 0.8.0
скана нет вовсе — он заработал уже после выпуска. Получается, в README
написано «проверяем», а посмотреть отчёт человеку негде.

Запускается по кнопке для любого тега. Дальше нужды в нём нет: начиная
со следующего выпуска ссылка появляется сама при сборке.

Разбор данных вынесен в чистые функции и проверяется в `отчёт_test.py`:
скрипт живёт на сервере, где одна попытка отладки стоит пять минут.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

РЕПОЗИТОРИЙ = "lamver/konspekt-releases"
# Сколько ждать разбора. Семьдесят движков смотрят файл по очереди, на
# 56 МБ это обычно три-четыре минуты.
ОЖИДАНИЕ = 15
ПОПЫТОК = 60


def гх(*аргументы: str) -> str:
    """Позвать gh.

    Без shell=True: на Linux он запускает первый элемент списка через
    оболочку, а остальные теряет, и gh молча выполняется без аргументов.
    На Windows тот же вызов работает, поэтому поломка вылезает только на
    сервере — ровно там, где отлаживать дороже всего.
    """
    готово = subprocess.run(
        ["gh", *аргументы], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if готово.returncode != 0:
        raise SystemExit(f"gh не смог: {готово.stderr.strip()}")
    return готово.stdout.strip()


def вт(путь: str, ключ: str) -> dict:
    запрос = urllib.request.Request(
        f"https://www.virustotal.com/api/v3/{путь}",
        headers={"x-apikey": ключ},
    )
    with urllib.request.urlopen(запрос, timeout=120) as ответ:
        return json.load(ответ)


def сумма_из_файла(содержимое: str) -> str:
    """Достать сумму из SHA256SUMS.

    Формат строки «сумма  имя». Перепутать поля местами легко, а
    последствие тихое: в VirusTotal уйдёт запрос по имени файла, тот
    ответит «не видел», и скрипт зальёт установщик заново вместо того,
    чтобы найти готовый отчёт. Поэтому проверяем, что похоже на sha256.
    """
    поля = содержимое.split()
    сумма = поля[0].lower() if поля else ""
    if len(сумма) != 64 or not all(з in "0123456789abcdef" for з in сумма):
        raise SystemExit(f"в SHA256SUMS не нашлась сумма: {содержимое[:80]!r}")
    return сумма


def уже_дописано(описание: str, сумма: str) -> bool:
    return f"virustotal.com/gui/file/{сумма}" in описание


def собрать_описание(описание: str, сумма: str, итог: dict) -> str:
    """Дописать строку с отчётом в конец описания релиза.

    Подозрительные складываем с вредоносными: движки часто ставят не
    «вирус», а «подозрительно», и написать «0 из 70», когда пятеро
    насторожились, значит соврать ровно в том месте, ради которого
    человек открыл отчёт.
    """
    ссылка = f"https://www.virustotal.com/gui/file/{сумма}"
    тревог = итог.get("malicious", 0) + итог.get("suspicious", 0)
    всего = sum(з for з in итог.values() if isinstance(з, int))
    значок = (
        f"[![VirusTotal](https://img.shields.io/badge/VirusTotal-"
        f"{тревог}%2F{всего}-{'brightgreen' if тревог == 0 else 'orange'}"
        f"?logo=virustotal&logoColor=white)]({ссылка})"
    )
    return (
        описание.rstrip()
        + "\n\n---\n\n"
        + f"{значок}\n\n"
        + f"Scanned by VirusTotal: {тревог} of {всего} engines flagged this "
        + f"file. Verify the build came from our source:\n\n"
        + "```\ngh attestation verify konspekt-*-setup.exe --repo lamver/konspekt\n```\n"
    )


def найти_отчёт(сумма: str, ключ: str) -> dict | None:
    try:
        данные = вт(f"files/{сумма}", ключ)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return данные["data"]["attributes"]["last_analysis_stats"]


def залить(тег: str, ключ: str) -> str:
    """Отправить установщик в VirusTotal, вернуть номер разбора."""
    with tempfile.TemporaryDirectory() as папка:
        гх("release", "download", тег, "--repo", РЕПОЗИТОРИЙ,
           "--pattern", "*.exe", "--dir", папка)
        файлы = list(Path(папка).glob("*.exe"))
        if not файлы:
            raise SystemExit(f"в релизе {тег} нет установщика")
        файл = файлы[0]
        print(f"скачан {файл.name}, {файл.stat().st_size / 1e6:.0f} МБ")

        # Файлы больше 32 МБ заливаются по особому адресу, обычный их не
        # примет. Установщик весит 56 МБ, так что это не про запас.
        адрес = вт("files/upload_url", ключ)["data"]
        граница = "----konspekt"
        тело = (
            f"--{граница}\r\n"
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{файл.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + файл.read_bytes() + f"\r\n--{граница}--\r\n".encode()

        запрос = urllib.request.Request(
            адрес, data=тело,
            headers={
                "x-apikey": ключ,
                "content-type": f"multipart/form-data; boundary={граница}",
            },
        )
        print("заливаем, это займёт минуту")
        with urllib.request.urlopen(запрос, timeout=900) as ответ:
            return json.load(ответ)["data"]["id"]


def дождаться(разбор: str, ключ: str) -> dict:
    for попытка in range(ПОПЫТОК):
        time.sleep(ОЖИДАНИЕ)
        состояние = вт(f"analyses/{разбор}", ключ)["data"]["attributes"]
        if состояние["status"] == "completed":
            return состояние["stats"]
        print(f"ждём разбора ({попытка + 1})")
    raise SystemExit("разбор не закончился за отведённое время")


def main() -> int:
    ключ = os.environ.get("VT_API_KEY", "")
    if not ключ:
        print("нужен VT_API_KEY в окружении")
        return 1
    тег = sys.argv[1] if len(sys.argv) > 1 else "v0.8.0"

    # Сумма берётся из SHA256SUMS релиза, а не считается по своей сборке:
    # проверять надо ровно тот файл, который скачивает человек.
    суммы = гх("release", "download", тег, "--repo", РЕПОЗИТОРИЙ,
               "--pattern", "SHA256SUMS", "--output", "-")
    сумма = сумма_из_файла(суммы)
    print(f"сумма выложенного файла: {сумма}")

    описание = гх("release", "view", тег, "--repo", РЕПОЗИТОРИЙ,
                  "--json", "body", "--jq", ".body")
    if уже_дописано(описание, сумма):
        print("ссылка на отчёт уже стоит в описании, делать нечего")
        return 0

    итог = найти_отчёт(сумма, ключ)
    if итог is None:
        print("VirusTotal этот файл не видел, отправляем на скан")
        итог = дождаться(залить(тег, ключ), ключ)
    print(f"итог разбора: {итог}")

    гх("release", "edit", тег, "--repo", РЕПОЗИТОРИЙ,
       "--notes", собрать_описание(описание, сумма, итог))
    print(f"ссылка добавлена в описание {тег}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

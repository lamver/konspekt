"""Прогнать через VirusTotal установщик, который уже лежит в релизе.

Обычная сборка сканирует свежесобранный файл, а у людей на руках тот,
что выложен: суммы у них разные, сборка не воспроизводима. Для 0.8.0
скана нет вовсе — он заработал после выпуска. Получается, в README
написано «проверяем», а посмотреть отчёт человеку негде.

Запускается руками по кнопке для любого тега. Дальше нужды в нём нет:
начиная со следующего выпуска отчёт появляется сам при сборке.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

РЕПОЗИТОРИЙ = "lamver/konspekt-releases"


def гх(*аргументы: str) -> str:
    готово = subprocess.run(
        ["gh", *аргументы], capture_output=True, text=True, shell=True,
        encoding="utf-8", errors="replace",
    )
    if готово.returncode != 0:
        raise SystemExit(f"gh не смог: {готово.stderr.strip()}")
    return готово.stdout.strip()


def вт(путь: str, ключ: str, данные: bytes | None = None) -> dict:
    запрос = urllib.request.Request(
        f"https://www.virustotal.com/api/v3/{путь}",
        data=данные,
        headers={"x-apikey": ключ},
    )
    with urllib.request.urlopen(запрос, timeout=120) as ответ:
        return json.load(ответ)


def main() -> int:
    ключ = os.environ.get("VT_API_KEY", "")
    if not ключ:
        print("нужен VT_API_KEY в окружении")
        return 1
    тег = sys.argv[1] if len(sys.argv) > 1 else "v0.8.0"

    # Сумма берётся из SHA256SUMS релиза, а не считается по скачанному
    # файлу: так проверяем ровно то, что получит человек, и заодно не
    # качаем 56 МБ ради одной строки.
    суммы = гх("release", "download", тег, "--repo", РЕПОЗИТОРИЙ,
               "--pattern", "SHA256SUMS", "--output", "-")
    сумма = суммы.split()[0]
    print(f"сумма выложенного файла: {сумма}")

    try:
        отчёт = вт(f"files/{сумма}", ключ)
        итог = отчёт["data"]["attributes"]["last_analysis_stats"]
        print(f"VirusTotal уже знает этот файл: {итог}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        print("VirusTotal файла не видел, отправляем на скан")
        return отправить(тег, ключ, сумма)

    return дописать(тег, сумма, итог)


def отправить(тег: str, ключ: str, сумма: str) -> int:
    """Залить файл в VirusTotal и дождаться разбора."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as папка:
        гх("release", "download", тег, "--repo", РЕПОЗИТОРИЙ,
           "--pattern", "*.exe", "--dir", папка)
        файл = next(Path(папка).glob("*.exe"))
        print(f"скачан {файл.name}, {файл.stat().st_size / 1e6:.0f} МБ")

        # Файлы больше 32 МБ заливаются по особому адресу, обычный их
        # не примет. Установщик весит 56 МБ, так что это не запас.
        адрес = вт("files/upload_url", ключ)["data"]
        граница = "----konspekt"
        тело = (
            f"--{граница}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{файл.name}"\r\n'
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
            разбор = json.load(ответ)["data"]["id"]

    # Разбор идёт минуты: семьдесят движков по очереди смотрят файл.
    for попытка in range(60):
        time.sleep(15)
        состояние = вт(f"analyses/{разбор}", ключ)["data"]["attributes"]
        if состояние["status"] == "completed":
            print(f"разбор готов: {состояние['stats']}")
            return дописать(тег, сумма, состояние["stats"])
        print(f"ждём разбора ({попытка + 1})")
    print("разбор не закончился за пятнадцать минут")
    return 1


def дописать(тег: str, сумма: str, итог: dict) -> int:
    """Добавить ссылку на отчёт в описание релиза."""
    ссылка = f"https://www.virustotal.com/gui/file/{сумма}"
    плохих = итог.get("malicious", 0) + итог.get("suspicious", 0)
    всего = sum(v for v in итог.values() if isinstance(v, int))

    описание = гх("release", "view", тег, "--repo", РЕПОЗИТОРИЙ,
                  "--json", "body", "--jq", ".body")
    if ссылка in описание:
        print("ссылка на отчёт уже стоит в описании")
        return 0

    кусок = (
        f"\n\n---\n\n"
        f"**VirusTotal:** [{плохих} of {всего} engines flagged this file]"
        f"({ссылка}) · "
        f"`gh attestation verify konspekt-*-setup.exe --repo lamver/konspekt`\n"
    )
    новое = описание.rstrip() + кусок
    гх("release", "edit", тег, "--repo", РЕПОЗИТОРИЙ, "--notes", новое)
    print(f"ссылка добавлена в описание {тег}: {ссылка}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

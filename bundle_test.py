"""Собранный exe обязан уметь распознавать речь.

Жалоба пользователя (issue #1) вскрыла ошибку, которой в разработке
не видно вовсе: библиотека `onnx_asr` на первой строке спрашивает свою
версию через importlib.metadata, а PyInstaller кладёт код без папки
`.dist-info`. В окружении разработчика метаданные есть, тесты зелёные,
всё работает. В установленной программе импорт падает, распознавание не
поднимается, и человек видит пустой транскрипт без единого сообщения.

Поэтому проверяем не исходники, а именно готовую сборку: запускаем
внутри неё импорт и загрузку модели.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import subprocess
import json
import os
import re
import sys
import tomllib
from pathlib import Path

DIST = Path("dist/Konspekt")
EXE = DIST / "Konspekt.exe"

FAILS = []


def check(ok: bool, message: str) -> None:
    print(("[ok] " if ok else "[FAIL] ") + message)
    if not ok:
        FAILS.append(message)


def main() -> int:
    if not EXE.exists():
        print("[пропуск] сборки нет, сначала packaging/build.py")
        return 0

    internal = DIST / "_internal"
    check((internal / "onnx_asr").is_dir(), "код onnx_asr попал в сборку")

    # Главное: метаданные. Без них импорт падает, но каталог с кодом
    # на месте, и по составу файлов беды не видно.
    meta = list(internal.glob("onnx_asr-*.dist-info")) + \
        list(internal.glob("onnx_asr*.dist-info")) + \
        list(internal.glob("onnx*asr*.dist-info"))
    check(bool(meta), "метаданные onnx-asr на месте (без них импорт падает)")

    # Версию библиотека берёт из METADATA: без этого файла падает даже
    # при наличии каталога .dist-info.
    if meta:
        check((meta[0] / "METADATA").exists(),
              "в метаданных есть METADATA, из которого читается версия")

    # Тот же класс поломки грозит любой зависимости: та же строчка
    # importlib.metadata.version может появиться в любой из них при
    # очередном обновлении. Поэтому список не пишем руками, а берём
    # из pyproject.toml — новая зависимость проверится сама.
    зависимости = tomllib.loads(
        Path("pyproject.toml").read_text(encoding="utf-8")
    )["project"]["dependencies"]
    for строка in зависимости:
        имя = re.split(r"[\[<>=!~;\s]", строка, maxsplit=1)[0]
        # Каталог .dist-info называется по нормализованному имени пакета:
        # дефисы, точки и заглавные буквы превращаются в подчёркивания и
        # нижний регистр (PEP 503 и PEP 427). Для winrt-Windows.UI.
        # Notifications это winrt_windows_ui_notifications, и сравнение
        # «в лоб» не находило его, хотя библиотека лежала на месте.
        нормальное = re.sub(r"[-_.]+", "_", имя).lower()
        # Ищем без учёта регистра: старые колёса писали имя как есть.
        нашлось = any(
            re.sub(r"[-_.]+", "_", п.name.split("-")[0]).lower() == нормальное
            for п in internal.glob("*.dist-info")
        )
        check(нашлось, f"метаданные {имя} на месте")

    # Метаданные — не единственный способ недоложить библиотеку. У
    # py3langid обученная модель лежит отдельным файлом data/model.plzma,
    # и PyInstaller не кладёт её сам: это не .py. Каталог с кодом при
    # этом на месте, метаданные тоже, а первая же сверка языка по тексту
    # падает уже у пользователя. Та же болезнь, что в issue #1, только
    # вместо метаданных данные.
    check((internal / "py3langid" / "data" / "model.plzma").exists(),
          "модель py3langid на месте (без неё сверка языка падает)")

    # Локализация: словари лежат в web/i18n и грузятся через мост. Если
    # сборщик их не подхватит, программа поднимется как ни в чём не
    # бывало и покажет ключи вместо текста — беда, которую по составу
    # exe не видно, а человек увидит сразу.
    i18n = internal / "web" / "i18n"
    check(i18n.is_dir(), "папка словарей web/i18n попала в сборку")
    if i18n.is_dir():
        for язык in ("ru", "en", "es", "sr"):
            словарь = i18n / f"{язык}.json"
            есть = словарь.exists()
            check(есть, f"словарь {язык}.json попал в сборку")
            if есть:
                # Пустой или обрезанный файл — это интерфейс из голых
                # ключей. Проверяем не факт наличия, а содержимое.
                try:
                    данные = json.loads(словарь.read_text(encoding="utf-8"))
                except Exception as e:  # noqa: BLE001
                    check(False, f"словарь {язык}.json читается ({e})")
                    continue
                check(
                    isinstance(данные, dict)
                    and "prefs" in данные
                    and "recording" in данные,
                    f"в словаре {язык}.json есть разделы интерфейса",
                )

    # Наличие файла ещё не значит, что библиотека его найдёт: путь к
    # модели строится от __file__, а PyInstaller его подменяет. И
    # проверить это снаружи нельзя — код библиотек лежит внутри exe, а
    # импорт здесь молча возьмётся из окружения разработчика и соврёт.
    # Поэтому спрашиваем саму сборку: она проверяет себя изнутри и
    # пишет отчёт в файл (окна и консоли у неё нет).
    отчёт = Path("_самопроверка.txt").resolve()
    отчёт.unlink(missing_ok=True)
    окружение = dict(os.environ, KONSPEKT_ОТЧЁТ=str(отчёт))
    subprocess.run([str(EXE), "--самопроверка"], env=окружение, timeout=300)
    строки = отчёт.read_text(encoding="utf-8").splitlines() \
        if отчёт.exists() else ["сборка ничего не ответила"]
    for строка in строки:
        print("  сборка: " + строка)
    отчёт.unlink(missing_ok=True)
    check(not any(s.startswith("[FAIL]") for s in строки) and len(строки) > 1,
          "сборка прошла самопроверку изнутри "
          "(распознавание, сверка языка, окно)")

    if FAILS:
        print(f"\nПровалено проверок: {len(FAILS)}")
        return 1
    print("\nСборка укомплектована: распознавание в ней поднимется")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

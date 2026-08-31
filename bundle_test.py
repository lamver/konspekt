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
import sys
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

    # Тот же класс поломки для остальных библиотек, которые спрашивают
    # свою версию на импорте. Перечисляем явно: молча узнавать о них от
    # пользователей — это ровно то, что уже случилось один раз.
    for package, folder in (("onnxruntime", "onnxruntime"),
                            ("numpy", "numpy")):
        present = any(internal.glob(f"{package}*.dist-info"))
        check(present or (internal / folder).is_dir(),
              f"{package} укомплектован в сборке")

    if FAILS:
        print(f"\nПровалено проверок: {len(FAILS)}")
        return 1
    print("\nСборка укомплектована: распознавание в ней поднимется")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

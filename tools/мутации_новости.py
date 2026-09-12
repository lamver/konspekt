"""Мутации: ловит ли version_test ошибку, из-за которой словарь уехал в 0.10.0.

Каждая мутация — правдоподобная поломка `собрать_новость`. Если проверка
её пропускает, она ничего не стережёт.
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ФАЙЛ = Path("tools/обновить_latest.py")

МУТАЦИИ = [
    (
        "вернуть сравнение версий (ровно ошибка 0.10.0)",
        "    if not аргументы.всё_равно:",
        "    if is_newer(ЗНАЕТ_ПРО_ПЕРЕВОДЫ, версия) and not аргументы.всё_равно:",
    ),
    (
        "порог сдвинут на несуществующую версию",
        'ЗНАЕТ_ПРО_ПЕРЕВОДЫ = "0.8.1"',
        'ЗНАЕТ_ПРО_ПЕРЕВОДЫ = "0.9.0"',
    ),
    (
        "согласие больше не спрашивается вовсе",
        "    if not аргументы.всё_равно:",
        "    if False:",
    ),
    (
        "пустая новость проходит молча",
        'raise SystemExit("не задано ни одной новости: нужен хотя бы --ru")',
        "return ''",
    ),
    (
        "один русский текст едет словарём",
        '    if list(переводы) == ["ru"]:\n        return переводы["ru"]',
        '    if False:\n        return переводы["ru"]',
    ),
]


def прогон() -> bool:
    """True, если проверка прошла."""
    итог = subprocess.run([sys.executable, "version_test.py"],
                          capture_output=True, text=True, encoding="utf-8")
    return итог.returncode == 0


def main() -> int:
    целые = ФАЙЛ.read_text(encoding="utf-8")
    запас = ФАЙЛ.with_suffix(".py.запас")
    shutil.copy(ФАЙЛ, запас)

    поймано = 0
    try:
        if not прогон():
            print("[плохо] проверка падает и без мутаций")
            return 1
        print("[ок] на целом коде проверка проходит")

        for имя, было, стало in МУТАЦИИ:
            if было not in целые:
                print(f"[плохо] мутация неприменима: {имя}")
                return 1
            ФАЙЛ.write_text(целые.replace(было, стало, 1), encoding="utf-8")
            if прогон():
                print(f"[плохо] мутация прошла незамеченной: {имя}")
                return 1
            поймано += 1
            print(f"[ок] поймана: {имя}")
    finally:
        shutil.copy(запас, ФАЙЛ)
        запас.unlink()

    print(f"\nМутации: {поймано}/{len(МУТАЦИИ)}. Проверка стережёт формат latest.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

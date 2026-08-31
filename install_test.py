"""Установленная копия должна жить в своей папке, а не во временной.

Из-за чего написано. Пробная установка из временного каталога
переучивала настоящую: Inno Setup запоминает каталог по AppId, и все
следующие обновления уезжали в `%TEMP%`, откуда каталог потом удалялся.
Программа у человека при этом оставалась старой навсегда, а выглядело
это как «обновление установилось, но ничего не изменилось». Вечер
поисков ушёл на то, чтобы связать одно с другим.

Проверка идёт по реестру, потому что именно оттуда установщик берёт
каталог, а не из того, что мы про него думаем.
"""

import testenv  # noqa: F401  русский вывод в консоли Windows

import sys
from pathlib import Path

# Тот же AppId, что в packaging/konspekt.iss. Расходиться им нельзя:
# тогда проверка будет смотреть не на ту установку.
КЛЮЧ = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\{8C4A6F42-6E0B-4F0E-9E0E-6D1B3B5B7A21}_is1"
)

ПРОВАЛЫ: list[str] = []


def проверить(условие: bool, что: str) -> None:
    print(("[ok] " if условие else "[FAIL] ") + что)
    if not условие:
        ПРОВАЛЫ.append(что)


def каталог_установки() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, КЛЮЧ) as ключ:
            return winreg.QueryValueEx(ключ, "InstallLocation")[0]
    except OSError:
        return None


def main() -> int:
    if sys.platform != "win32":
        print("[пропуск] проверка про установщик Windows")
        return 0

    место = каталог_установки()
    if not место:
        print("[пропуск] Konspekt не установлен, проверять нечего")
        return 0

    путь = Path(место)
    print(f"установлен в: {путь}")

    временные = [Path(p) for p in (
        Path.home() / "AppData" / "Local" / "Temp",
    )]
    во_временной = any(
        путь.is_relative_to(в) for в in временные
    )
    проверить(not во_временной,
              "установка не указывает во временную папку "
              "(иначе обновления пропадают вместе с ней)")

    проверить(путь.is_dir(),
              "каталог установки существует (иначе обновиться некуда)")

    if ПРОВАЛЫ:
        print(f"\nПровалено проверок: {len(ПРОВАЛЫ)}")
        print("Починить: переустановить с явным каталогом,")
        print(r"  konspekt-X.Y.Z-setup.exe /DIR=%LOCALAPPDATA%\Konspekt")
        return 1
    print("\nУстановка на месте: обновления дойдут до человека")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

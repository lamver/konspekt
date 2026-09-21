# -*- coding: utf-8 -*-
"""Мутации: ломаем починку окна и ждём, что проверка это заметит.

Проверка, которая не падает на сломанном коде, ничего не доказывает.
Каждая мутация — отдельная беда, от которой мы защищаемся.
"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).parent.parent
ПРОВЕРКА = КОРЕНЬ / "окно_на_экране_test.py"

# (файл, что заменить, на что, описание беды)
МУТАЦИИ = [
    (
        "app/core/settings.py",
        "    if abs(x) >= 30000 or abs(y) >= 30000:\n        return False\n",
        "",
        "перестали отсеивать координаты свёрнутого окна",
    ),
    (
        "app/core/settings.py",
        "        if видно_вширь >= ВИДНО_ПО_ШИРИНЕ and видно_ввысь >= ВИДНО_ПО_ВЫСОТЕ:",
        "        if видно_вширь >= 1 and видно_ввысь >= 1:",
        "годным считается окно, от которого виден один пиксель",
    ),
    (
        "app/core/settings.py",
        "    if not экраны:\n        return True\n    for эx",
        "    if not экраны:\n        return True\n    return True\n    for эx",
        "перестали смотреть, попадает ли окно хоть на один монитор",
    ),
    (
        "app/core/settings.py",
        "    if not экраны:\n        return (None, None)\n    эx, эy, эш, эв = экраны[0]",
        "    if not экраны:\n        return (0, 0)\n    эx, эy, эш, эв = экраны[0]",
        "без списка экранов ставим окно наугад в угол",
    ),
    (
        "app/core/settings.py",
        "    return (эx + max(0, (эш - width) // 2), эy + max(0, (эв - height) // 2))",
        "    return (эx + эш, эy + эв)",
        "возвращаем потерянное окно за правый нижний угол",
    ),
    (
        "app/core/service.py",
        "        if not settings_mod.геометрия_годится(x, y, width, height):\n"
        "            log.debug(\"Не запоминаем положение окна x=%s y=%s: оно вне экрана\", x, y)\n"
        "            return\n",
        "",
        "снова записываем в настройки положение свёрнутого окна",
    ),
    (
        "app/core/settings.py",
        "    if width < МИН_ШИРИНА or height < МИН_ВЫСОТА:\n        return False\n",
        "",
        "окно размером в полоску заголовка считается годным",
    ),
    (
        "app/core/settings.py",
        "    if geom.width < МИН_ШИРИНА or geom.height < МИН_ВЫСОТА:\n"
        "        geom.width, geom.height = целое.width, целое.height\n"
        "        чинили = True\n",
        "",
        "перестали возвращать окну рабочий размер",
    ),
    (
        "app/core/settings.py",
        "    if not геометрия_годится(geom.x, geom.y, geom.width, geom.height, экраны):\n"
        "        geom.x, geom.y = поставить_по_центру(geom.width, geom.height, экраны)\n"
        "        чинили = True\n",
        "",
        "перестали возвращать окну годное место",
    ),
    (
        "app/core/settings.py",
        "    целое = WindowGeometry()\n    чинили = False\n",
        "    целое = WindowGeometry()\n    return False\n    чинили = False\n",
        "починка геометрии всегда отвечает «всё в порядке»",
    ),
    (
        "app/ui/window.py",
        "        if not settings_mod.починить_геометрию(geom, экраны):\n"
        "            return geom.x, geom.y\n",
        "        return geom.x, geom.y\n",
        "окно при запуске перестало чинить сохранённую геометрию",
    ),
    (
        "app/ui/win32.py",
        "    по_порядку = sorted(найденные, key=lambda м: not м[4])",
        "    по_порядку = list(найденные)",
        "основной монитор перестал быть первым в списке",
    ),
    (
        "app/ui/win32.py",
        "    k = масштаб or 1.0",
        "    k = 1.0",
        "перестали пересчитывать экраны из физических пикселей в логические",
    ),
]


def прогнать() -> int:
    r = subprocess.run(
        [sys.executable, "-X", "utf8", str(ПРОВЕРКА)],
        cwd=str(КОРЕНЬ), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return r.returncode


print("сначала убеждаемся, что на целом коде проверка проходит")
if прогнать() != 0:
    print("ПЛОХО: проверка падает ещё до мутаций")
    raise SystemExit(1)
print("  ок, проходит\n")

поймано = 0
for имя, было, стало, беда in МУТАЦИИ:
    файл = КОРЕНЬ / имя
    исходник = файл.read_text(encoding="utf-8")
    if было not in исходник:
        print(f"[ПЛОХО] {беда}: не нашли что ломать в {имя}")
        continue
    файл.write_text(исходник.replace(было, стало, 1), encoding="utf-8")
    try:
        код = прогнать()
    finally:
        файл.write_text(исходник, encoding="utf-8")
    if код != 0:
        поймано += 1
        print(f"[OK   ] поймано: {беда}")
    else:
        print(f"[ПЛОХО] НЕ поймано: {беда}")

print(f"\nмутаций поймано: {поймано} из {len(МУТАЦИИ)}")
sys.exit(0 if поймано == len(МУТАЦИИ) else 1)

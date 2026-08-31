"""Разовая правка: поднять номер версии в pyproject.toml и app/__init__.py.

Питоном, а не PowerShell: тот дописывает в начало файла невидимую метку
и ломает чтение pyproject.toml.
"""

import re
import sys
import tomllib
from pathlib import Path

новая = sys.argv[1]
итог: list[str] = []

пути = {
    Path("pyproject.toml"): re.compile(r'(?m)^version = "[^"]+"'),
    Path("app/__init__.py"): re.compile(r'(?m)^__version__ = "[^"]+"'),
}
замены = {
    Path("pyproject.toml"): f'version = "{новая}"',
    Path("app/__init__.py"): f'__version__ = "{новая}"',
}

for путь, образец in пути.items():
    текст = путь.read_text(encoding="utf-8")
    новый, сколько = образец.subn(замены[путь], текст)
    if сколько != 1:
        итог.append(f"{путь}: не нашёл строку версии, ничего не меняю")
        continue
    путь.write_text(новый, encoding="utf-8", newline="\n")
    итог.append(f"{путь}: {новая}")

данные = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
итог.append("pyproject читается, версия " + данные["project"]["version"])
Path("_res.txt").write_text("\n".join(итог), encoding="utf-8")

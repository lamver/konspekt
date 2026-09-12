# -*- coding: utf-8 -*-
"""Страница честно показывает выложенную версию.

Сама проверка написана на JS и гоняет настоящий код из docs/index.html
вместе с настоящими словарями docs/i18n.js: беда живёт на публичной
странице, и проверять её пересказом на питоне бессмысленно. Здесь только
запуск, чтобы прогон видел её наравне с остальными.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import shutil
import subprocess
import sys
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent
node = shutil.which("node")
if not node:
    print("[skip] node не найден, версия на странице не проверена")
    raise SystemExit(0)

итог = subprocess.run(
    [node, str(КОРЕНЬ / "версия_на_странице_test.js")],
    cwd=КОРЕНЬ, capture_output=True, text=True, encoding="utf-8", errors="replace",
)
print(итог.stdout + итог.stderr, end="")
sys.exit(итог.returncode)

# -*- coding: utf-8 -*-
"""Счёт проверки на вирусы читается из релиза честно.

Сама проверка написана на JS и гоняет настоящий разбор из
docs/index.html: беда живёт на публичной странице, и проверять её
пересказом на питоне бессмысленно. Здесь только запуск, чтобы прогон
видел её наравне с остальными.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import shutil
import subprocess
import sys
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent
node = shutil.which("node")
if not node:
    print("[skip] node не найден, виджет проверки не проверен")
    raise SystemExit(0)

итог = subprocess.run(
    [node, str(КОРЕНЬ / "виджет_проверки_test.js")],
    cwd=КОРЕНЬ, capture_output=True, text=True, encoding="utf-8", errors="replace",
)
print(итог.stdout + итог.stderr, end="")
sys.exit(итог.returncode)

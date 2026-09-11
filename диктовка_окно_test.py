# -*- coding: utf-8 -*-
"""Диктовка в поле вопроса: сторона окна.

Сама проверка написана на JS и гоняет настоящий web/app.js в node.
Здесь только запуск, чтобы прогон видел её наравне с остальными.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import shutil
import subprocess
import sys
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent
node = shutil.which("node")
if not node:
    print("[skip] node не найден, диктовка в окне не проверена")
    raise SystemExit(0)

итог = subprocess.run(
    [node, str(КОРЕНЬ / "диктовка_поле_test.js")],
    cwd=КОРЕНЬ, capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=180,
)
print(итог.stdout + итог.stderr, end="")
sys.exit(итог.returncode)

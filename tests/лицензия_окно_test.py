# -*- coding: utf-8 -*-
"""Плашка «Купить лицензию» и масштаб в окне.

Сама проверка написана на JS и гоняет настоящий web/app.js в node:
плашку и масштаб делает фронт. Здесь только запуск, чтобы прогон
видел её наравне с остальными.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import shutil
import subprocess
import sys
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent
node = shutil.which("node")
if not node:
    # Молчать нельзя: пропажу проверки заметят только по вернувшейся беде.
    print("[skip] node не найден, плашка и масштаб в окне не проверены")
    raise SystemExit(0)

итог = subprocess.run(
    [node, str(КОРЕНЬ / "tests" / "лицензия_окно_test.js")],
    cwd=КОРЕНЬ, capture_output=True, text=True, encoding="utf-8", errors="replace",
)
print(итог.stdout + итог.stderr, end="")
sys.exit(итог.returncode)

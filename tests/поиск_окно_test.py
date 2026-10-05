# -*- coding: utf-8 -*-
"""Выдача поиска в окне: лучшие первыми и найденные слова подсвечены.

Сама проверка написана на JS и гоняет настоящий web/app.js в node:
порядок и подсветку делает фронт. Здесь только запуск, чтобы прогон
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
    print("[skip] node не найден, выдача поиска в окне не проверена")
    raise SystemExit(0)

итог = subprocess.run(
    [node, str(КОРЕНЬ / "tests" / "поиск_окно_test.js")],
    cwd=КОРЕНЬ, capture_output=True, text=True, encoding="utf-8", errors="replace",
)
print(итог.stdout + итог.stderr, end="")
sys.exit(итог.returncode)

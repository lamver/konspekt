# -*- coding: utf-8 -*-
"""Заметки и ответы можно забрать из программы, а не перепечатывать.

Сама проверка написана на JS и гоняет настоящий web/app.js в node:
копирование целиком живёт во фронте, и проверять его пересказом на
питоне бессмысленно. Здесь только запуск, чтобы прогон видел её
наравне с остальными.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import shutil
import subprocess
import sys
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent
node = shutil.which("node")
if not node:
    # Node есть не везде. Молчать нельзя: иначе пропажу проверки заметят
    # только по вернувшейся беде.
    print("[skip] node не найден, копирование не проверено")
    raise SystemExit(0)

итог = subprocess.run(
    [node, str(КОРЕНЬ / "копирование_test.js")],
    cwd=КОРЕНЬ, capture_output=True, text=True, encoding="utf-8", errors="replace",
)
print(итог.stdout + итог.stderr, end="")
sys.exit(итог.returncode)

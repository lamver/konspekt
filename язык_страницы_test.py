# -*- coding: utf-8 -*-
"""Язык страницы проекта должен угадываться по домену.

Одна и та же страница отдаётся с трёх адресов, и konspekt.aisearch.ru
заведён как русский. Пришедший туда должен увидеть русский, а не
английский по языку своего браузера.

Сама проверка написана на JS и гоняет настоящий pickLang из
docs/index.html в node: беда во фронте, и проверять её пересказом на
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
    print("[skip] node не найден, выбор языка страницы не проверен")
    raise SystemExit(0)

итог = subprocess.run(
    [node, str(КОРЕНЬ / "язык_страницы_test.js")],
    cwd=КОРЕНЬ, capture_output=True, text=True, encoding="utf-8", errors="replace",
)
print(итог.stdout + итог.stderr, end="")
sys.exit(итог.returncode)

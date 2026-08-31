"""Общая обвязка для тестов: русский вывод в консоли Windows.

Консоль Windows по умолчанию живёт в cp1251, и `print` с кириллицей
роняет тест с `UnicodeEncodeError` уже после того, как все проверки
прошли. Выглядит как поломка кода, хотя сломана только кодировка вывода.

`run_tests.py` задаёт кодировку через переменные окружения, но тест
часто запускают в одиночку, когда чинишь именно его. Импорт этого
модуля первой строкой закрывает оба случая.

    import testenv  # noqa: F401
"""

from __future__ import annotations

import sys

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

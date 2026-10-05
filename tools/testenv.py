"""Инструментам нужна та же обвязка, что и проверкам: русский вывод и
чужой каталог данных. Живёт она в tests/testenv.py, здесь только
переходник, чтобы `import testenv` из tools/ находил её, а не копию.
"""
import importlib.util
import sys
from pathlib import Path

_путь = Path(__file__).resolve().parent.parent / "tests" / "testenv.py"
_описание = importlib.util.spec_from_file_location("testenv", _путь)
_модуль = importlib.util.module_from_spec(_описание)
sys.modules["testenv"] = _модуль
_описание.loader.exec_module(_модуль)

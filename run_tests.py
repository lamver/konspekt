"""Прогоны тестов: быстрые и медленные.

Один скрипт вместо десяти команд в документации.

    python run_tests.py         # только быстрые
    python run_tests.py --full  # все тесты
    python run_tests.py --list  # показать список

Быстрые: до 2 секунд, не трогают диски и звук, прогоняются на каждый push.
Медленные: живые звук, модели, длинные импорты, прогоняются один раз в сутки.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

FAST = [
    "smoke_test.py",
    "web_test.py",
    "models_test.py",
    "router_test.py",
    "langid_test.py",
    "voices_test.py",
    "embedder_test.py",
    "drop_test.py",
    "people_test.py",
    "llm_download_test.py",
    "migration_test.py",
]

SLOW = [
    "llm_test.py",
    "summary_test.py",
    "long_meeting_test.py",
    "audiofile_test.py",
    "restart_test.py",
    "enroll_test.py",
    "import_test.py",
    "old_meeting_test.py",
    "whisper_test.py",
    "long_import_test.py",
]


def run(test: str) -> int:
    print(f"\n=== {test} ===")
    return subprocess.run([sys.executable, test], cwd=ROOT).returncode


def main() -> int:
    parser = argparse.ArgumentParser(prog="run_tests")
    parser.add_argument("--full", action="store_true", help="прогнать и медленные тоже")
    parser.add_argument("--list", action="store_true", help="показать список тестов")
    args = parser.parse_args()

    tests = FAST.copy()
    if args.full:
        tests += SLOW

    if args.list:
        print("Быстрые:")
        for t in FAST:
            print(f"  ✅ {t}")
        print("\nМедленные:")
        for t in SLOW:
            print(f"  ⏱️  {t}")
        print(f"\nВсего: {len(FAST) + len(SLOW)} тестов")
        return 0

    failed = 0
    for test in tests:
        if run(test) != 0:
            failed += 1

    print(f"\n==== Итог: {len(tests) - failed} OK, {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

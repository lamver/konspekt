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
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

FAST = [
    "smoke_test.py",
    "web_test.py",
    "models_test.py",
    "router_test.py",
    "vad_test.py",
    "relang_test.py",
    "langui_test.py",
    "asrset_test.py",
    "draft_test.py",
    "langid_test.py",
    "voices_test.py",
    "embedder_test.py",
    "drop_test.py",
    "people_test.py",
    "llm_download_test.py",
    "resume_test.py",
    "notice_test.py",
    "delete_test.py",
    "search_test.py",
    "clip_test.py",
    "import_clip_test.py",
    "nomodel_test.py",
    "broken_model_test.py",
    "bundle_test.py",
    "upgrade_test.py",
    "version_test.py",
    "update_test.py",
    "migration_test.py",
    "profile_test.py",
    "install_test.py",
]

SLOW = [
    "job_test.py",
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
    "latency_test.py",
]


def run(test: str) -> int:
    # flush обязателен: при выводе в файл питон буферизует свой print, и
    # заголовок теста оказывается после всего, что напечатал сам тест.
    print(f"\n=== {test} ===", flush=True)
    # Консоль Windows живёт в cp1251, и русский вывод теста роняет его
    # с UnicodeEncodeError, хотя сам тест прошёл. Просим utf-8 у всех.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run([sys.executable, test], cwd=ROOT, env=env).returncode


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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

    failed: list[str] = []
    for test in tests:
        if run(test) != 0:
            failed.append(test)

    print(f"\n==== Итог: {len(tests) - len(failed)} OK, {len(failed)} FAIL", flush=True)
    # Имена упавших: без них в длинном прогоне приходится листать вывод
    # и глазами искать, что именно сломалось.
    for test in failed:
        print(f"  упал: {test}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

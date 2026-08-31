"""Сервер модели не должен переживать приложение.

llama-server держит в памяти несколько гигабайт. Если он остаётся жить
после падения приложения или снятия задачи, память не возвращается до
перезагрузки, а следующий запуск поднимает ещё один такой же. За вечер
работы так набралось пять серверов и 27 ГБ.

Проверяем по-настоящему: запускаем дочерний процесс, который поднимает
сервер и убивает себя без всякой уборки, и смотрим, ушёл ли сервер.
Заодно контрольный опыт без привязки, чтобы убедиться, что уборку
делает именно job-объект, а не случайность.

Тест медленный: каждый запуск читает с диска 1.8 ГБ весов.
"""

from __future__ import annotations

import testenv  # noqa: F401  русский вывод в консоли Windows

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from app.llm.local import find_model

# Дочерний процесс: поднимает сервер, печатает его PID и умирает от
# SIGTERM, то есть без atexit, finally и прочей уборки.
CHILD = """
import os, signal, sys, time
sys.path.insert(0, {root!r})
from app.llm import local
{disable}
server = local.LocalServer(local.find_model())
server.ensure_started()
print(server._proc.pid, flush=True)
time.sleep(1)
os.kill(os.getpid(), signal.SIGTERM)
"""


def _alive(pid: int) -> bool:
    """Жив ли процесс с таким номером."""
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True, text=True,
    ).stdout
    return str(pid) in out


def _run_child(attach: bool) -> int:
    """Запустить дочерний процесс и вернуть PID поднятого им сервера."""
    disable = "" if attach else "local._attach_to_job = lambda proc: None"
    code = CHILD.format(root=str(ROOT), disable=disable)
    res = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT, capture_output=True, text=True,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        timeout=300,
    )
    line = (res.stdout or "").strip().splitlines()
    assert line, f"дочерний процесс не запустил сервер:\n{res.stderr[-2000:]}"
    return int(line[-1])


def main() -> int:
    if os.name != "nt":
        print("[skip] job-объекты есть только на Windows")
        return 0

    if find_model() is None:
        print("[skip] модель не скачана")
        return 0

    # --- как в бою: сервер обязан уйти вместе с приложением ------------
    pid = _run_child(attach=True)
    time.sleep(3)
    assert not _alive(pid), (
        f"сервер {pid} пережил смерть приложения и держит память"
    )
    print(f"[ok] сервер {pid} ушёл вместе с приложением")

    # --- контрольный опыт: без привязки он выживает --------------------
    # Без этой половины тест зелёный даже если job вообще не работает,
    # а сервер умер по другой причине.
    stray = _run_child(attach=False)
    time.sleep(3)
    survived = _alive(stray)
    if survived:
        subprocess.run(["taskkill", "/F", "/PID", str(stray)],
                       capture_output=True)
    assert survived, (
        "контрольный сервер тоже умер: значит проверка ничего не доказывает, "
        "уборку делает не job-объект"
    )
    print(f"[ok] без привязки сервер {stray} выживает, значит убирает именно job")

    print("\nСервер модели не переживает приложение.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Собрать дистрибутив Konspekt целиком: папку и установщик.

    python packaging/build.py            # папка и установщик
    python packaging/build.py --no-setup # только папка, быстрее

Отдельный скрипт, а не строчка в документации: команд четыре, каждая со
своими флагами, и вручную их рано или поздно запустят по-разному. Версия
берётся из app/__init__.py, чтобы она не разъезжалась между программой,
установщиком и именем файла.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import __version__  # noqa: E402

DIST = ROOT / "dist"
SPEC = ROOT / "packaging" / "konspekt.spec"
ISS = ROOT / "packaging" / "konspekt.iss"
ENGINE_SRC = ROOT / "models" / "llm" / "engine"

# Из сборки llama.cpp нужен только сервер: там ещё десяток утилит
# (bench, quantize, cli), это лишние 13 МБ.
ENGINE_KEEP = {
    "llama-server.exe",
    "llama-server",
    "llama-server-impl.dll",
    "llama.dll",
    "llama-common.dll",
    "mtmd.dll",
    "ggml.dll",
    "ggml-base.dll",
    "ggml-rpc.dll",
    "libomp.dll",
}

# Inno Setup ставится в одно из трёх мест и в PATH себя не прописывает.
# winget без прав администратора кладёт его в профиль пользователя.
ISCC_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
]


def find_iscc() -> Path | None:
    found = shutil.which("iscc")
    if found:
        return Path(found)
    for path in ISCC_CANDIDATES:
        if path.exists():
            return path
    return None


def run(cmd: list[str]) -> None:
    print(">", " ".join(str(c) for c in cmd), flush=True)
    # utf-8 дочерним процессам: иначе русский вывод make_icon.py и
    # PyInstaller падает на кодировке консоли Windows.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    result = subprocess.run(cmd, cwd=ROOT, env=env)
    if result.returncode != 0:
        raise SystemExit(f"не удалось: {cmd[0]} (код {result.returncode})")


def folder_size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024 / 1024


def copy_engine(app_dir: Path) -> None:
    """Положить llama.cpp рядом с приложением.

    Копируем сами, а не через datas в spec: PyInstaller считает своими все
    .dll из datas и раскладывает их копии ещё и в корень сборки. На движке
    это давало 140 МБ дубликатов вместо 31 МБ.

    Движок едет в дистрибутиве, чтобы заметки работали сразу после
    установки, без отдельной докачки.
    """
    if not ENGINE_SRC.is_dir():
        print("движок llama.cpp не найден, заметки потребуют докачки")
        return
    dest = app_dir / "_internal" / "engine"
    dest.mkdir(parents=True, exist_ok=True)
    for item in ENGINE_SRC.iterdir():
        # Варианты ggml-cpu-* нужны все: llama.cpp выбирает подходящий по
        # набору инструкций процессора уже во время запуска.
        if item.name in ENGINE_KEEP or item.name.startswith("ggml-cpu-"):
            shutil.copy2(item, dest / item.name)
    size = sum(f.stat().st_size for f in dest.iterdir()) / 1024 / 1024
    print(f"движок: {size:.0f} МБ")


def main() -> int:
    # Консоль Windows живёт в cp1251, и русский вывод обрывает сборку
    # посреди работы, хотя собралось всё правильно.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-setup", action="store_true", help="только папка, без установщика")
    parser.add_argument("--console", action="store_true", help="собрать с консолью, чтобы видеть ошибки запуска")
    args = parser.parse_args()

    print(f"Konspekt {__version__}")

    # Значок рисуется из того же кода, что и значок в трее: держать их
    # порознь значит рано или поздно показать разные картинки.
    run([sys.executable, str(ROOT / "packaging" / "make_icon.py")])

    env_console = os.environ.get("KONSPEKT_CONSOLE")
    if args.console:
        os.environ["KONSPEKT_CONSOLE"] = "1"
    elif env_console:
        # Иначе случайно оставленная переменная окружения тихо соберёт
        # релиз с чёрным окном консоли.
        del os.environ["KONSPEKT_CONSOLE"]

    run([sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm",
         "--distpath", str(DIST), "--workpath", str(ROOT / "build")])

    app_dir = DIST / "Konspekt"
    exe = app_dir / "Konspekt.exe"
    if not exe.exists():
        raise SystemExit("PyInstaller отработал, но exe нет")

    copy_engine(app_dir)
    print(f"папка: {app_dir} ({folder_size_mb(app_dir):.0f} МБ)")

    if args.no_setup:
        return 0

    iscc = find_iscc()
    if iscc is None:
        print("Inno Setup не найден, установщик пропущен.")
        print("  winget install --id JRSoftware.InnoSetup -e")
        return 0

    run([str(iscc), f"/DAppVersion={__version__}", str(ISS)])
    setup = DIST / f"konspekt-{__version__}-setup.exe"
    if setup.exists():
        print(f"установщик: {setup} ({setup.stat().st_size / 1024 / 1024:.0f} МБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

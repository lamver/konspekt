# -*- coding: utf-8 -*-
"""Мутации для обещаний: главное — ловится ли забытая задача."""
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).parent.parent
ПРОВЕРКА_ФАЙЛ = КОРЕНЬ / "обещания_test.py"
ПРОВЕРКА = [sys.executable, str(ПРОВЕРКА_ФАЙЛ)]
СТОРОЖ = КОРЕНЬ / "app" / "audio" / "сторож.py"
СЛОВАРЬ = КОРЕНЬ / "dist" / "Konspekt" / "_internal" / "web" / "i18n" / "es.json"
APPJS = КОРЕНЬ / "dist" / "Konspekt" / "_internal" / "web" / "app.js"

провал = 0


def прогнать(имя: str) -> int:
    ответ = subprocess.run(ПРОВЕРКА, capture_output=True, cwd=КОРЕНЬ, timeout=180)
    return ответ.returncode


# 1. Забыли завести проверку на закрытую задачу. Ради этого свойства всё
#    и затевалось: раньше проверка бодро отчитывалась «9/9», пока №26
#    тихо оставалась без присмотра.
целое = ПРОВЕРКА_ФАЙЛ.read_bytes()
текст = целое.decode("utf-8")
# Вырезаем весь блок про #26.
новый = re.sub(
    r"# --- #26 .*?\n\)\n", "", текст, flags=re.DOTALL
)
if новый == текст:
    print("[СЛОМАНА] не нашёл блок #26, чтобы его выбросить")
    провал += 1
else:
    try:
        ПРОВЕРКА_ФАЙЛ.write_bytes(новый.encode("utf-8"))
        код = прогнать("забытая задача")
    finally:
        ПРОВЕРКА_ФАЙЛ.write_bytes(целое)
    if код == 0:
        print("[ВЫЖИЛА] забытая закрытая задача не замечена")
        провал += 1
    else:
        print("[убита] забытая закрытая задача замечена")

# 2. Пропал сторож (обещание №28).
целое = СТОРОЖ.read_bytes()
try:
    СТОРОЖ.unlink()
    код = прогнать("нет сторожа")
finally:
    СТОРОЖ.write_bytes(целое)
print("[убита] пропавший сторож замечен" if код else "[ВЫЖИЛА] пропавший сторож")
провал += 0 if код else 1

# 3. Из сборки пропал испанский словарь (обещание №2).
целое = СЛОВАРЬ.read_bytes()
try:
    СЛОВАРЬ.unlink()
    код = прогнать("нет словаря")
finally:
    СЛОВАРЬ.write_bytes(целое)
print("[убита] пропавший словарь замечен" if код else "[ВЫЖИЛА] пропавший словарь")
провал += 0 if код else 1

# 4. В сборку уехало окно без копирования и диктовки (обещание №26).
целое = APPJS.read_bytes()
try:
    APPJS.write_bytes(целое.replace(b"toggleFieldDictation", b"nothingHere"))
    код = прогнать("старое окно")
finally:
    APPJS.write_bytes(целое)
print("[убита] старое окно в сборке замечено" if код else "[ВЫЖИЛА] старое окно")
провал += 0 if код else 1

print()
print(f"итог: {4 - провал}/4")
sys.exit(1 if провал else 0)

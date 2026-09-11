# -*- coding: utf-8 -*-
"""Мутации для имён записей: ломаем и ждём падения проверки."""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).parent.parent
ФАЙЛ = КОРЕНЬ / "app" / "asr" / "audiofile.py"
ПРОВЕРКА = [sys.executable, str(КОРЕНЬ / "имена_записей_test.py")]

МУТАЦИИ = [
    (
        "вернулись к голому имени файла",
        "        if имя and not похоже_на_идентификатор(имя):\n            return имя",
        "        if True:\n            return имя",
    ),
    (
        "переименовываем вообще всё",
        "        if имя and not похоже_на_идентификатор(имя):",
        "        if False:",
    ),
    (
        "примета пропала, записи сливаются",
        "        примета = _примета(имя)",
        '        примета = ""',
    ),
    (
        "во времени пропали цифры",
        '    return time.strftime("%d.%m %H:%M", time.localtime(когда))',
        '    return "недавно"',
    ),
    (
        "uuid больше не узнаём",
        "        [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}  # uuid\n",
        "",
    ),
    (
        "«audio (1)» больше не узнаём",
        "    (\\s*\\(\\d+\\))?$                         # «audio (1)», копия из проводника",
        "    $",
    ),
]

ЦЕЛО = ФАЙЛ.read_bytes()
провал = 0
try:
    for имя, было, стало in МУТАЦИИ:
        б = было.encode("utf-8")
        с = стало.encode("utf-8")
        if ЦЕЛО.count(б) != 1:
            print(f"[СЛОМАНА] {имя}: образец встречается {ЦЕЛО.count(б)} раз")
            провал += 1
            continue
        ФАЙЛ.write_bytes(ЦЕЛО.replace(б, с))
        try:
            ответ = subprocess.run(ПРОВЕРКА, capture_output=True, cwd=КОРЕНЬ, timeout=120)
            код = ответ.returncode
        finally:
            ФАЙЛ.write_bytes(ЦЕЛО)
        if код == 0:
            print(f"[ВЫЖИЛА] {имя}")
            провал += 1
        else:
            print(f"[убита] {имя}")
finally:
    ФАЙЛ.write_bytes(ЦЕЛО)

print()
print(f"итог: {len(МУТАЦИИ) - провал}/{len(МУТАЦИИ)}")
sys.exit(1 if провал else 0)

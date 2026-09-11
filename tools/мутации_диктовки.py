# -*- coding: utf-8 -*-
"""Мутации для диктовки в поле: ломаем и ждём падения проверок."""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).parent.parent
APPJS = КОРЕНЬ / "web" / "app.js"
SERVICE = КОРЕНЬ / "app" / "core" / "service.py"
PY = sys.executable

JS = ["node", str(КОРЕНЬ / "диктовка_поле_test.js")]
PYT = [PY, str(КОРЕНЬ / "диктовка_поле_test.py")]

МУТАЦИИ = [
    (APPJS, "вопрос уходит сразу, без правки",
     b"    \xd0\xb2\xd1\x81\xd1\x82\xd0\xb0\xd0\xb2\xd0\xb8\xd1\x82\xd1\x8c\xd0\x92\xd0\x9f\xd0\xbe\xd0\xbb\xd0\xb5(ui.chatText, \xd1\x82\xd0\xb5\xd0\xba\xd1\x81\xd1\x82);",
     b"    sendQuestion();",
     JS),
    (APPJS, "продиктованное затирает написанное",
     b"  \xd0\xbf\xd0\xbe\xd0\xbb\xd0\xb5.value = \xd1\x81\xd0\xbb\xd0\xb5\xd0\xb2\xd0\xb0 + \xd1\x80\xd0\xb0\xd0\xb7\xd0\xb4\xd0\xb5\xd0\xbb\xd0\xb8\xd1\x82\xd0\xb5\xd0\xbb\xd1\x8c + \xd1\x82\xd0\xb5\xd0\xba\xd1\x81\xd1\x82 + \xd1\x81\xd0\xbf\xd1\x80\xd0\xb0\xd0\xb2\xd0\xb0;",
     b"  \xd0\xbf\xd0\xbe\xd0\xbb\xd0\xb5.value = \xd1\x82\xd0\xb5\xd0\xba\xd1\x81\xd1\x82;",
     JS),
    (APPJS, "слова слипаются без пробела",
     b"  const \xd1\x80\xd0\xb0\xd0\xb7\xd0\xb4\xd0\xb5\xd0\xbb\xd0\xb8\xd1\x82\xd0\xb5\xd0\xbb\xd1\x8c = \xd1\x81\xd0\xbb\xd0\xb5\xd0\xb2\xd0\xb0 && !/\\s$/.test(\xd1\x81\xd0\xbb\xd0\xb5\xd0\xb2\xd0\xb0) ? ' ' : '';",
     b"  const \xd1\x80\xd0\xb0\xd0\xb7\xd0\xb4\xd0\xb5\xd0\xbb\xd0\xb8\xd1\x82\xd0\xb5\xd0\xbb\xd1\x8c = '';",
     JS),
    (APPJS, "молчим о причине отказа",
     b"    showToast(\xd0\xbf\xd1\x80\xd0\xb8\xd1\x87\xd0\xb8\xd0\xbd\xd0\xb0 ? `${t('chat.dictate_failed')}: ${\xd0\xbf\xd1\x80\xd0\xb8\xd1\x87\xd0\xb8\xd0\xbd\xd0\xb0}`\n                      : t('chat.dictate_failed'));",
     b"    ;",
     JS),
    (APPJS, "кнопка не показывает, что слушает",
     b"  ui.chatMic.classList.toggle('is-listening', \xd1\x80\xd0\xb5\xd0\xb6\xd0\xb8\xd0\xbc === '\xd1\x81\xd0\xbb\xd1\x83\xd1\x88\xd0\xb0\xd0\xb5\xd1\x82');",
     b"  ;",
     JS),
    (APPJS, "на пустом распознавании молчим",
     b"      showToast(t('chat.dictate_empty'));",
     b"      ;",
     JS),
    (SERVICE, "текст печатается в чужое окно",
     b"        \xd1\x82\xd0\xb5\xd0\xba\xd1\x81\xd1\x82 = \xd1\x81\xd0\xbb\xd1\x83\xd0\xb6\xd0\xb1\xd0\xb0.\xd1\x80\xd0\xb0\xd1\x81\xd0\xbf\xd0\xbe\xd0\xb7\xd0\xbd\xd0\xb0\xd1\x82\xd1\x8c_\xd0\xb4\xd0\xbb\xd1\x8f_\xd0\xbe\xd0\xba\xd0\xbd\xd0\xb0()",
     b"        \xd1\x82\xd0\xb5\xd0\xba\xd1\x81\xd1\x82 = \xd1\x81\xd0\xbb\xd1\x83\xd0\xb6\xd0\xb1\xd0\xb0.\xd0\xb7\xd0\xb0\xd0\xba\xd0\xbe\xd0\xbd\xd1\x87\xd0\xb8\xd1\x82\xd1\x8c()",
     PYT),
    (SERVICE, "диктуем во время записи встречи",
     b"        \xd0\xb1\xd0\xb5\xd0\xb4\xd0\xb0 = \xd1\x81\xd0\xbb\xd1\x83\xd0\xb6\xd0\xb1\xd0\xb0._\xd0\xbf\xd0\xbe\xd1\x87\xd0\xb5\xd0\xbc\xd1\x83_\xd0\xbd\xd0\xb5\xd0\xbb\xd1\x8c\xd0\xb7\xd1\x8f()",
     b"        \xd0\xb1\xd0\xb5\xd0\xb4\xd0\xb0 = \"\"",
     PYT),
    (SERVICE, "отмена не доходит до службы",
     b"            \xd1\x81\xd0\xbb\xd1\x83\xd0\xb6\xd0\xb1\xd0\xb0.\xd0\xbe\xd1\x82\xd0\xbc\xd0\xb5\xd0\xbd\xd0\xb8\xd1\x82\xd1\x8c()",
     b"            pass",
     PYT),
]

цело = {APPJS: APPJS.read_bytes(), SERVICE: SERVICE.read_bytes()}
провал = 0
try:
    for файл, имя, было, стало, команда in МУТАЦИИ:
        исходник = цело[файл]
        if исходник.count(было) != 1:
            print(f"[СЛОМАНА] {имя}: образец встречается {исходник.count(было)} раз")
            провал += 1
            continue
        файл.write_bytes(исходник.replace(было, стало))
        try:
            ответ = subprocess.run(команда, capture_output=True, cwd=КОРЕНЬ, timeout=180)
            код = ответ.returncode
        finally:
            файл.write_bytes(исходник)
        if код == 0:
            print(f"[ВЫЖИЛА] {имя}")
            провал += 1
        else:
            print(f"[убита] {имя}")
finally:
    for файл, данные in цело.items():
        файл.write_bytes(данные)

print()
print(f"итог: {len(МУТАЦИИ) - провал}/{len(МУТАЦИИ)}")
sys.exit(1 if провал else 0)

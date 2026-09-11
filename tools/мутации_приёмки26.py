# -*- coding: utf-8 -*-
"""Мутации для живой приёмки №26: ловит ли она настоящие поломки."""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).parent.parent
APPJS = КОРЕНЬ / "web" / "app.js"
HTML = КОРЕНЬ / "web" / "index.html"
SERVICE = КОРЕНЬ / "app" / "core" / "service.py"
ПРОВЕРКА = [sys.executable, str(КОРЕНЬ / "приёмка26_test.py")]

МУТАЦИИ = [
    (HTML, "кнопку копирования убрали из разметки",
     'id="summary-copy"', 'id="summary-copy-NET"'),
    (HTML, "микрофон убрали из поля вопроса",
     'id="chat-mic"', 'id="chat-mic-NET"'),
    (APPJS, "копируем нарисованное вместо разметки",
     "      копировать(текст, 'copy.done');",
     "      копировать('', 'copy.done');"),
    (APPJS, "в «без разметки» осталась разметка",
     "      копировать(markdownToPlain(текст), 'copy.done_plain');",
     "      копировать(текст, 'copy.done_plain');"),
    (APPJS, "в цитате пропало имя говорящего",
     "      копировать(цитатаРеплики(who.textContent, time.textContent, seg.text),",
     "      копировать(цитатаРеплики('', '', seg.text),"),
    (APPJS, "продиктованное не попадает в поле",
     "    вставитьВПоле(ui.chatText, текст);",
     "    ;"),
    (APPJS, "кнопка не показывает, что слушает",
     "  ui.chatMic.classList.toggle('is-listening', режим === 'слушает');",
     "  ;"),
    (APPJS, "причину отказа проглотили",
     "    showToast(причина ? `${t('chat.dictate_failed')}: ${причина}`\n                      : t('chat.dictate_failed'));",
     "    ;"),
    (APPJS, "словарь не грузим, человек видит ключи",
     "      const dict = await api.get_i18n_dict(lang);",
     "      const dict = {};"),
]

цело = {APPJS: APPJS.read_bytes(), HTML: HTML.read_bytes(),
        SERVICE: SERVICE.read_bytes()}
провал = 0
try:
    for файл, имя, было, стало in МУТАЦИИ:
        исходник = цело[файл]
        б, с = было.encode("utf-8"), стало.encode("utf-8")
        если_много = исходник.count(б)
        if если_много != 1:
            print(f"[СЛОМАНА] {имя}: образец встречается {если_много} раз")
            провал += 1
            continue
        файл.write_bytes(исходник.replace(б, с))
        try:
            ответ = subprocess.run(ПРОВЕРКА, capture_output=True, cwd=КОРЕНЬ,
                                   timeout=300)
            код = ответ.returncode
            # «Пропуск» значит, что окно не поднялось: это не победа.
            пропуск = b"[skip]" in ответ.stdout
        finally:
            файл.write_bytes(исходник)
        if пропуск:
            print(f"[?] {имя}: окно не поднялось, ничего не проверили")
            провал += 1
        elif код == 0:
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

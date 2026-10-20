# -*- coding: utf-8 -*-
"""Язык встречи выбирается у самой встречи (20 октября).

Проба собеседования по-русски уехала в литовский и сербский по паре
коротких фраз: определитель переключил движок на Whisper, расшифровка
встала, итоги вышли по-сербски. Человек заранее знал язык встречи, но
сказать это было негде.

Что сторожим:

1. Язык хранится у встречи и ходит через API вместе с «по умолчанию».
2. С зафиксированным языком маршрутизатор не зовёт определитель и не
   пересматривает язык по тексту; с «auto» всё как раньше.
3. Старт записи фиксирует язык в распознавании, остановка снимает;
   смена языка посреди записи применяется сразу.
4. Итоги пишутся на выбранном языке без голосования по репликам.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import testenv  # noqa: F401  песочница вместо боевого профиля

from app.asr.router import LanguageRouter
from app.core.models import Meeting, Speaker, TranscriptSegment
from app.core.service import AppService
from app.storage.db import Store

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


class Движок:
    def __init__(self, имя: str, текст: str) -> None:
        self.имя = имя
        self.текст = текст
        self.вызовов = 0
        self.подсказки: list[dict] = []

    def transcribe(self, pcm, sample_rate=16000, meeting_id="", offset=0.0, speaker="them", lang=None):
        self.вызовов += 1
        self.подсказки.append({"lang": lang})
        return [TranscriptSegment(meeting_id=meeting_id, text=self.текст, speaker=Speaker.THEM)]


class Определитель:
    def __init__(self) -> None:
        self.вызовов = 0

    def detect(self, pcm, sample_rate=16000):
        self.вызовов += 1
        return ("lt", 0.99)


звук = np.zeros(16000, dtype=np.float32)

# --- 2. Маршрутизатор ------------------------------------------------------
русский = Движок("ru", "Что такое горутины")
чужой = Движок("foreign", "Deffin ir tos funkciją")
опред = Определитель()
router = LanguageRouter(русский, опред, чужой, fallback_lang="ru")

сегменты = list(router.transcribe(звук, meeting_id="m", speaker="me"))
проверить(опред.вызовов == 1 and чужой.вызовов == 1 and сегменты[0].lang == "lt",
          "без фиксации определитель слушается: литовский ушёл в Whisper")

router.фиксированный = "ru"
сегменты = list(router.transcribe(звук, meeting_id="m", speaker="me"))
проверить(опред.вызовов == 1 and русский.вызовов == 1 and сегменты[0].lang == "ru",
          "с русским языком встречи определитель молчит, фраза идёт в GigaAM")

router.фиксированный = "en"
сегменты = list(router.transcribe(звук, meeting_id="m", speaker="me"))
проверить(опред.вызовов == 1 and чужой.вызовов == 2 and чужой.подсказки[-1] == {"lang": "en"}
          and сегменты[0].lang == "en",
          "с английским фраза идёт в Whisper с подсказкой языка, без определителя")

router.фиксированный = None
list(router.transcribe(звук, meeting_id="m", speaker="me"))
проверить(опред.вызовов == 2, "сняли фиксацию — определитель снова работает")

# --- 1, 3, 4. Сервис ---------------------------------------------------------
tmp = Path(tempfile.mkdtemp()) / "t.db"
service = AppService(store=Store(str(tmp)))
try:
    service.transcriber = LanguageRouter(Движок("ru", "x"), Определитель(), Движок("f", "y"), fallback_lang="ru")
    service.settings.asr.language = "ru"
    service.settings.asr.detect_language = True
    mid = service.store.create_meeting(Meeting(title="Собеседование")).id
    данные = service.get_meeting(mid)
    проверить(данные["language"] == "" and данные["language_default"] == "auto",
              "новая встреча: язык «как в настройках», сейчас это auto")

    service.settings.asr.detect_language = False
    проверить(service.get_meeting(mid)["language_default"] == "ru",
              "определение выключили — по умолчанию русский")

    service.update_meeting(mid, language="sr")
    проверить(service.store.get_meeting(mid).language == "sr", "язык встречи сохранился в базе")
    service.update_meeting(mid, language="klingon")
    проверить(service.store.get_meeting(mid).language == "", "незнакомый код не записывается")
    service.update_meeting(mid, language="en")

    service.active_meeting_id = mid
    service._применить_язык_записи(mid)
    проверить(service.transcriber.фиксированный == "en", "старт записи фиксирует язык встречи")
    service.update_meeting(mid, language="ru")
    проверить(service.transcriber.фиксированный == "ru", "смена языка посреди записи применяется сразу")
    service.update_meeting(mid, language="auto")
    проверить(service.transcriber.фиксированный is None, "«определять по фразам» снимает фиксацию")
    service._применить_язык_записи(None)
    service.active_meeting_id = None

    # Итоги: без выбора голосуют реплики, с выбором — язык встречи.
    service.update_meeting(mid, language="")
    service.settings.asr.detect_language = True
    for текст, lang in [("Deffin ir tos dvieršaiščių funkciją metodą", "lt"), ("Что такое", "ru")]:
        service.store.add_segment(TranscriptSegment(meeting_id=mid, text=текст, lang=lang, speaker=Speaker.ME))
    проверить(service._язык_итогов(mid) == "lt", "без выбора итоги идут за репликами (тут литовский мусор)")
    service.update_meeting(mid, language="ru")
    проверить(service._язык_итогов(mid) == "ru", "с выбранным языком итоги на нём, без голосования")
finally:
    service.store.close()

if БЕДЫ:
    print(f"\nБед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("\nЯзык встречи выбирает человек, и обрывки его больше не сбивают.")

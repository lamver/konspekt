# -*- coding: utf-8 -*-
"""Итоги на языке встречи и сербская речь в Whisper.

Две беды, от которых это сторожит, обе выглядят как «программа не для
нас» в глазах человека не из России.

Первая: испанец записал испанскую встречу, а итоги и разборы пришли
по-русски, потому что в инструкции модели было жёстко «отвечай на
русском языке». Подписи разборов без модели («Кто сколько говорил»)
тоже были только русскими.

Вторая: сербская речь уходила в GigaAM, который знает только русский,
и встреча превращалась в русскую кашу.
"""
from __future__ import annotations

import testenv  # noqa: F401  русский вывод и песочница вместо боевого профиля

import tempfile
from pathlib import Path

import numpy as np

from app.asr.langid import CYRILLIC_LANGS
from app.asr.router import LanguageRouter
from app.asr.whisper import WhisperTranscriber, латиница
from app.core import analysis
from app.core.models import Speaker, TranscriptSegment
from app.core.язык_итогов import язык_встречи, язык_подписей
from app.llm import lenses, prompts

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


# --- язык встречи ---------------------------------------------------------------

проверить(язык_встречи([("es", "Hola, ¿cómo estáis? Empecemos por el presupuesto."), ("en", "ok")], "ru") == "es",
          "язык встречи — тот, на котором сказано больше текста, а не больше реплик")
проверить(язык_встречи([("en", "ok"), ("en", "yes"), ("ru", "Давайте подробно обсудим бюджет на следующий квартал")]) == "ru",
          "десяток коротких «ok» не перевешивает длинный доклад")
проверить(язык_встречи([], "sr") == "sr", "встреча без расшифровки: язык интерфейса")
проверить(язык_встречи([("", "старая реплика без языка")], "en") == "en",
          "реплики без пометки языка не считаются")
проверить(язык_встречи([("en-US", "Hello there everyone")], "ru") == "en", "«en-US» — это английский")
проверить(язык_подписей("de", "es") == "es", "немецкая встреча: подписи на языке интерфейса")
проверить(язык_подписей("sr", "ru") == "sr", "сербская встреча: подписи по-сербски")

# --- инструкции модели ----------------------------------------------------------

def система(сообщения) -> str:
    return сообщения[0]["content"]


рус = prompts.summary_messages("Планёрка", "Маша: привет")
проверить("Отвечай на русском языке." in система(рус), "русская встреча: инструкция прежняя, слово в слово")
исп = prompts.summary_messages("Reunión", "Marta: hola", lang="es")
проверить("русском" not in система(исп) and "испанском языке" in система(исп),
          "испанская встреча: итоги велено писать по-испански")
проверить("español" in исп[1]["content"][-120:], "в конце запроса просьба на языке самой встречи: так модель слушается лучше")
проверить("заголовки" in система(исп).lower(), "про перевод заголовков разделов сказано прямо")
серб = prompts.summary_messages("Sastanak", "Milica: zdravo", lang="sr")
проверить("латиницей" in система(серб), "сербские итоги — латиницей, как сербский интерфейс")
проверить("английском" in система(prompts.chunk_messages("x", "en")), "куски длинной встречи — на её языке")
проверить("английском" in система(prompts.merge_messages("t", ["a"], lang="en")),
          "сводка частей длинной встречи — на её языке")

for вид, р in lenses.РАЗРЕЗЫ.items():
    if р.engine != "модель" or вид == "summary":
        continue
    с = lenses.messages(вид, "", "x", lang="en")
    проверить("русском" not in система(с) and "English" in с[1]["content"]
              and "translate the headings" in с[1]["content"],
              f"разбор «{вид}» английской встречи пишется по-английски")
с = lenses.messages("sales", "", "x")
проверить("Отвечай на русском языке." in система(с) and "заголовки оставь как есть" in с[1]["content"],
          "разбор русской встречи — как раньше")

# --- разборы без модели ---------------------------------------------------------

реплики = [
    {"text": "Hola, ¿empezamos con el presupuesto?", "start": 0, "end": 5, "who": "Marta", "track": "me"},
    {"text": "Sí, tengo las cifras del trimestre.", "start": 5.2, "end": 9, "who": "Daniel", "track": "them"},
]
текст = analysis.разговор(реплики, "es")["markdown"]
проверить("Quién habló cuánto" in текст and "Кто" not in текст, "«Разбор разговора» испанской встречи по-испански")
проверить("Кто сколько говорил" in analysis.разговор(реплики)["markdown"], "по умолчанию — по-русски, как раньше")
for язык in ("ru", "en", "es", "sr"):
    проверить(set(analysis.ТЕКСТЫ[язык]) == set(analysis.ТЕКСТЫ["ru"]), f"у подписей «{язык}» все те же ключи")
тон = analysis.тон(реплики, "es")["markdown"]
проверить("ruso" in тон and "сомнение" not in тон,
          "тон испанской речи не считается русскими словарями, а честно сказано почему")

# --- сервис: язык берётся из самой встречи --------------------------------------

from app.audio import NullCapture  # noqa: E402
from app.core.service import AppService  # noqa: E402
from app.storage.db import Store  # noqa: E402

папка = Path(tempfile.mkdtemp(prefix="konspekt-язык-"))
сервис = AppService(store=Store(str(папка / "k.db")), capture=NullCapture(), transcriber=None)
сервис.settings.language = "ru"
ид = сервис.create_meeting("Reunión")["id"]
for i, (кто, фраза) in enumerate([("Marta", "Hola, ¿empezamos con el presupuesto del trimestre?"),
                                   ("Daniel", "Sí, tengo las cifras y una propuesta para recortar gastos.")]):
    сервис.store.add_segment(TranscriptSegment(
        meeting_id=ид, speaker=Speaker.ME if i == 0 else Speaker.THEM, text=фраза,
        start=i * 5.0, end=i * 5.0 + 4.5, lang="es", voice_id=f"v{i}", voice_label=кто))
проверить(сервис._язык_итогов(ид) == "es", "русский интерфейс, испанская встреча: итоги по-испански")
разговор = next(a for a in сервис.list_analyses(ид) if a["kind"] == "talk")
проверить("Quién habló cuánto" in разговор["text"], "карточка «Разбор разговора» испанской встречи по-испански")
сервис.shutdown()

# --- сербская речь идёт в Whisper ------------------------------------------------

проверить("sr" not in CYRILLIC_LANGS, "сербский больше не считается русским")
проверить(латиница("Добар дан, Љубица! Џеп, ђак, њива, Ћирић.") == "Dobar dan, Ljubica! Džep, đak, njiva, Ćirić.",
          "сербская кириллица переводится в латиницу буква к букве")


class Модель:
    """Подмена весов Whisper: запоминает подсказку языка."""

    def __init__(self, ответ: str) -> None:
        self.ответ = ответ
        self.язык: str | None = None

    def recognize(self, waveform, sample_rate=16000, language=None):
        if language == "xx":
            raise KeyError("<|xx|>")
        self.язык = language
        return self.ответ


class Русская:
    def __init__(self) -> None:
        self.звали = 0

    def transcribe(self, pcm, sample_rate=16000, meeting_id="", offset=0.0, speaker="them"):
        self.звали += 1
        return [TranscriptSegment(meeting_id=meeting_id, text="русская каша", start=offset, end=offset + 2)]


class Сербский:
    def detect(self, pcm, sample_rate=16000):
        return ("sr", 0.95)


whisper = WhisperTranscriber(Path("нет"), auto_load=False)
whisper._model = Модель("Добар дан, почињемо састанак.")
русская = Русская()
роутер = LanguageRouter(русская, Сербский(), whisper)
звук = np.zeros(16000 * 2, dtype=np.float32) + 0.01
куски = list(роутер.transcribe(звук, 16000, "m", 0.0, "them"))
проверить(русская.звали == 0, "сербская фраза не уходит в русскую модель")
проверить(whisper._model.язык == "sr", "Whisper получает подсказку «sr», а не гадает сам")
проверить(bool(куски) and куски[0].text == "Dobar dan, počinjemo sastanak." and куски[0].lang == "sr",
          "сербская фраза записана латиницей и помечена сербской")

whisper._model = Модель("hello")
list(whisper.transcribe(звук, 16000, lang="xx"))
проверить(whisper._model.язык is None, "язык, которого Whisper не знает: распознаём без подсказки, а не падаем")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Итоги пишутся на языке встречи, сербский идёт в Whisper.")

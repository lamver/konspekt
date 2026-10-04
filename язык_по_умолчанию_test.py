# -*- coding: utf-8 -*-
"""Язык по умолчанию и сербский с немецким в распознавании.

Беды, найденные замером на Google FLEURS 04.10:
- сербскую речь определитель языка раскидывал между sr, bs и hr, каждый
  ответ был неуверенным, и фраза уходила в русскую модель кашей: 86 %
  ошибок в словах, язык угадан в 2 фразах из 40;
- неуверенные немецкие фразы тоже уходили в русскую модель, потому что
  язык по умолчанию был русским у всех: 39 % ошибок против 10 %, когда
  язык назван;
- интерфейс при первом запуске был русским у всех.
"""
from __future__ import annotations

import testenv  # noqa: F401  русский вывод и песочница вместо боевого профиля

import numpy as np

from app.asr import langid
from app.asr.router import LanguageRouter
from app.core import settings as S
from app.core.models import TranscriptSegment

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


# --- Первый запуск по языкам Windows -------------------------------------

def первый(коды):
    s = S.по_языку_системы(S.Settings(), коды)
    return s.language, s.asr.language


проверить(первый(["en", "ru"]) == ("ru", "ru"),
          "английская Windows с русской раскладкой: русский, как у большинства наших")
проверить(первый(["de"]) == ("en", "de"),
          "немецкая Windows: английский интерфейс, немецкая речь")
проверить(первый(["en", "de"]) == ("en", "de"),
          "английская Windows с немецкой раскладкой: речь немецкая")
проверить(первый(["sr"]) == ("sr", "sr") and первый(["hr", "en"]) == ("sr", "sr"),
          "сербская и хорватская Windows: сербский интерфейс и речь")
проверить(первый(["es", "en"]) == ("es", "es"), "испанская Windows: испанский")
проверить(первый(["uk"]) == ("ru", "ru"), "украинская Windows: русская модель, она и украинскую речь пишет")
проверить(первый(["ja"]) == ("en", "en"), "незнакомый язык: английский")
проверить(первый(["en"]) == ("en", "en"), "только английский: английский")
проверить(первый([]) == ("ru", "ru"), "языки не прочитались: как раньше")
проверить(S.load().language == "ru", "проверки идут на русском, где бы ни собирались")


# --- Сербский, боснийский и хорватский — один язык -----------------------

class Модель:
    """Подменяет ONNX-сессию: отдаёт заранее заданные вероятности."""

    def __init__(self, вероятности):
        self.вероятности = вероятности

    def run(self, _outputs, _inputs):
        return [np.array(self.вероятности, dtype=np.float32)]


det = langid.LanguageDetector("_нет_такой_папки", auto_load=False)
det._labels = ["ru", "sr", "bs", "hr", "de", "lb", "en"]
det._features = lambda wave, sr: np.zeros((200, 60), dtype=np.float32)
звук = np.zeros(16000 * 3, dtype=np.float32)

det._session = Модель([0.02, 0.45, 0.40, 0.10, 0.01, 0.01, 0.01])
проверить(det.detect(звук) is not None and det.detect(звук)[0] == "sr",
          f"sr 0.45 + bs 0.40 + hr 0.10 — уверенный сербский: {det.detect(звук)}")
det._session = Модель([0.02, 0.02, 0.02, 0.02, 0.10, 0.80, 0.02])
проверить(det.detect(звук) is not None and det.detect(звук)[0] == "de",
          "люксембургский с немецким вместе — немецкий")
det._session = Модель([0.30, 0.20, 0.10, 0.05, 0.20, 0.10, 0.05])
проверить(det.detect(звук) is None, "неуверенный ответ по-прежнему не принимаем")


# --- Роутер: подсказка языка и язык по умолчанию -------------------------

class Распознаватель:
    def __init__(self, имя):
        self.имя = имя
        self.подсказки: list[str | None] = []

    def transcribe(self, pcm, sample_rate=16000, meeting_id="", offset=0.0,
                   speaker="them", lang=None):
        self.подсказки.append(lang)
        return [TranscriptSegment(meeting_id=meeting_id, start=offset, end=offset + 1,
                                  text=self.имя, speaker=speaker)]


class Определитель:
    def __init__(self):
        self.ответ = None

    def detect(self, pcm, sample_rate=16000):
        return self.ответ


def роутер(запасной):
    ru, whisper, d = Распознаватель("ru"), Распознаватель("whisper"), Определитель()
    return LanguageRouter(ru, d, whisper, fallback_lang=запасной), ru, whisper, d


р, ru, whisper, d = роутер("de")
куски = list(р.transcribe(звук))
проверить(куски[0].text == "whisper" and whisper.подсказки == ["de"],
          "немец, язык фразы не понят: немецкий Whisper с подсказкой, а не русская модель")
d.ответ = ("en", 0.97)
list(р.transcribe(звук))
проверить(whisper.подсказки[-1] == "en", "язык уверенно услышан — его и подсказываем")
d.ответ = None
list(р.transcribe(звук))
проверить(whisper.подсказки[-1] is None,
          "язык унаследован от прошлой фразы — не подсказываем: неверная подсказка заставит переводить")

р, ru, whisper, d = роутер("ru")
d.ответ = ("sr", 0.9)
list(р.transcribe(звук, speaker="them"))
d.ответ = None
list(р.transcribe(звук, speaker="them"))
проверить(whisper.подсказки == ["sr", "sr"], "сербский подсказываем всегда, Whisper его путает")
р.reset()
list(р.transcribe(звук, speaker="me"))
проверить(ru.подсказки == [None] and len(whisper.подсказки) == 2,
          "русский пользователь, язык не понят: русская модель, как раньше")

р, ru, whisper, d = роутер("de")
d.ответ = ("uk", 0.95)
куски = list(р.transcribe(звук))
проверить(куски[0].text == "ru" and куски[0].lang == "ru",
          f"украинская фраза у немца ушла в русскую модель и помечена русской, а не немецкой: {куски[0].lang}")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Язык по умолчанию берётся из Windows, сербский и немецкий распознаются своей моделью.")

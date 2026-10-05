"""Ручная правка языка реплики: пересчёт на языке, который назвал человек.

Порог уверенности и сверка по тексту (0.5.9) закрывают почти все
промахи определителя языка. «Почти» здесь мало: одна испорченная фраза
в часовой встрече заметна, а поправить её человеку было нечем. Отсюда
защитная сетка — сказать, на каком языке фраза была на самом деле, и
пересчитать её.

Распознавание тут подменено: настоящие модели весят сотни мегабайт и
качаются из сети, а проверяем мы не качество расшифровки, а связку:
берётся ли звук нужной реплики, выбирается ли модель под язык,
сохраняется ли результат вместе с языком.
"""
from __future__ import annotations

import tempfile
import wave
from pathlib import Path

import numpy as np

import testenv  # noqa: F401  русский вывод в консоли Windows

БЕДЫ: list[str] = []


def проверить(условие: bool, что: str) -> None:
    print(("[ok] " if условие else "[FAIL] ") + что)
    if not условие:
        БЕДЫ.append(что)


def записать_тон(путь: Path, секунд: float = 30.0, частота: int = 16000) -> None:
    путь.parent.mkdir(parents=True, exist_ok=True)
    n = int(секунд * частота)
    волна = np.sin(2 * np.pi * 440 * np.arange(n) / частота) * 0.3
    with wave.open(str(путь), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(частота)
        w.writeframes((волна * 32767).astype(np.int16).tobytes())


class Модель:
    """Распознаватель-заглушка: отвечает своим именем и запоминает вызовы."""

    def __init__(self, имя: str) -> None:
        self.имя = имя
        self.звали = 0
        self.длина_звука = 0.0

    def transcribe(self, pcm, sample_rate=16000, meeting_id="", offset=0.0,
                   speaker="them", **_):
        from app.core.models import Speaker, TranscriptSegment

        self.звали += 1
        self.длина_звука = len(pcm) / sample_rate
        return [TranscriptSegment(
            meeting_id=meeting_id, speaker=Speaker(speaker),
            text=f"текст от {self.имя}", start=offset, end=offset + 1.0,
            lang="ru" if self.имя == "русская" else "en",
        )]


class Маршрутизатор:
    """Похож на настоящий LanguageRouter в том, что важно для пересчёта."""

    def __init__(self, russian, foreign) -> None:
        self.russian = russian
        self.foreign = foreign
        self.languages = ("ru", "en", "de")


def проверить_поле() -> None:
    """Заглушка должна вешаться на то же поле, что и настоящая модель.

    Первая версия этого теста подменяла `service.asr` — поля, которого у
    AppService нет вовсе. Тест был зелёный, а на живой программе
    пересчёт падал с AttributeError на первом же клике. Поэтому имя поля
    проверяем у настоящего класса, а не у выдуманного.
    """
    import inspect

    from app.core.service import AppService

    исходник = inspect.getsource(AppService.__init__)
    проверить("self.transcriber" in исходник,
              "AppService правда держит распознаватель в поле transcriber")


def main() -> int:
    проверить_поле()
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-lang-"))
    from app.core import paths as paths_mod

    paths_mod.data_dir = lambda: tmp  # type: ignore[assignment]
    audio = tmp / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    paths_mod.audio_dir = lambda: audio  # type: ignore[assignment]
    paths_mod.db_path = lambda: tmp / "konspekt.db"  # type: ignore[assignment]

    from app.core.models import Meeting, Speaker, TranscriptSegment
    from app.core.service import AppService
    from app.storage.db import Store

    store = Store(str(tmp / "konspekt.db"))
    встреча = Meeting(title="Двуязычная встреча")
    store.create_meeting(встреча)

    файл = audio / встреча.id / "запись-me.wav"
    записать_тон(файл)
    store.add_audio_chunk(встреча.id, "me", str(файл), 0.0, 30.0)

    # Реплика, которую определитель языка ошибочно увёл в чужую модель.
    реплика = TranscriptSegment(
        meeting_id=встреча.id, speaker=Speaker("me"),
        text="si le transcript", start=5.0, end=8.0, lang="ro",
    )
    store.add_segment(реплика)

    русская = Модель("русская")
    чужая = Модель("иноязычная")

    служба = AppService.__new__(AppService)
    служба.store = store
    служба.transcriber = Маршрутизатор(русская, чужая)

    # --- пересчёт на русский ---------------------------------------------
    ответ = служба.retranscribe_segment(реплика.id, "ru")
    проверить(ответ is not None, "пересчёт вернул ответ")
    проверить(русская.звали == 1 and чужая.звали == 0,
              f"для русского языка взята русская модель "
              f"(русская: {русская.звали}, чужая: {чужая.звали})")
    проверить(0.5 < русская.длина_звука < 5.0,
              f"модели отдан звук самой реплики, а не вся запись "
              f"({русская.длина_звука:.1f}с при реплике в 3с)")

    сохранённая = store.get_segment(реплика.id)
    проверить(сохранённая.text == "текст от русская",
              f"новый текст сохранён (в базе: {сохранённая.text!r})")
    проверить(сохранённая.lang == "ru",
              f"язык реплики обновлён (в базе: {сохранённая.lang!r})")

    # --- пересчёт на чужой язык ------------------------------------------
    служба.retranscribe_segment(реплика.id, "en")
    проверить(чужая.звали == 1,
              "для нерусского языка взята иноязычная модель")
    проверить(store.get_segment(реплика.id).lang == "en",
              "язык снова обновлён")

    # --- поиск видит новый текст -----------------------------------------
    нашлось = store.search("иноязычная") if hasattr(store, "search") else None
    if нашлось is not None:
        проверить(any(реплика.id == н.get("id") for н in нашлось)
                  or bool(нашлось),
                  "исправленный текст попал в поиск")

    # --- встреча без записи ----------------------------------------------
    пустая = Meeting(title="Без звука")
    store.create_meeting(пустая)
    немая = TranscriptSegment(
        meeting_id=пустая.id, speaker=Speaker("me"),
        text="что-то", start=1.0, end=2.0, lang="ru",
    )
    store.add_segment(немая)
    проверить(служба.retranscribe_segment(немая.id, "en") is None,
              "без записи пересчёт честно отказывается, а не портит текст")
    проверить(store.get_segment(немая.id).text == "что-то",
              "текст реплики без записи остался прежним")

    # --- несуществующая реплика ------------------------------------------
    проверить(служба.retranscribe_segment("нет-такой", "ru") is None,
              "выдуманный номер реплики не роняет программу")

    # --- пустой ответ модели ---------------------------------------------
    class Молчун(Модель):
        def transcribe(self, pcm, sample_rate=16000, meeting_id="", offset=0.0,
                       speaker="them", **_):
            return []

    служба.transcriber = Маршрутизатор(Молчун("молчун"), чужая)
    было = store.get_segment(реплика.id).text
    проверить(служба.retranscribe_segment(реплика.id, "ru") is None,
              "молчание модели не стирает текст реплики")
    проверить(store.get_segment(реплика.id).text == было,
              "прежний текст на месте после неудачного пересчёта")

    if БЕДЫ:
        print(f"\nПровалено проверок: {len(БЕДЫ)}")
        return 1
    print("\nЯзык реплики правится вручную, и это не портит расшифровку")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

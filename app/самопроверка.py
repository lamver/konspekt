"""Самопроверка собранной программы: то, что видно только изнутри exe.

Обычные тесты запускаются в окружении разработчика, где установлено
всё подряд. Сборка устроена иначе: код библиотек лежит внутри exe, а
данные — файлами рядом. Проверка «импортируем библиотеку и посмотрим»
снаружи молча возьмёт её из окружения разработчика и скажет, что всё
хорошо, даже когда в сборке её нет вовсе. Ровно так выглядела issue #1:
тесты зелёные, а у человека распознавание молчало.

Поэтому проверяет сама программа, изнутри своей сборки. Вывод короткий
и на русском: его читает человек, запустивший проверку перед выпуском.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

БЕДЫ: list[str] = []
СТРОКИ: list[str] = []


def _проверить(условие: bool, что: str) -> None:
    СТРОКИ.append(("[ok] " if условие else "[FAIL] ") + что)
    if not условие:
        БЕДЫ.append(что)


def проверить() -> int:
    # Распознавание: onnx_asr на первой строке спрашивает свою версию
    # через importlib.metadata, и без метаданных импорт падает.
    try:
        import onnx_asr  # noqa: F401

        _проверить(True, "onnx_asr импортируется (распознавание поднимется)")
    except Exception as беда:
        _проверить(False, f"onnx_asr не импортируется: {беда}")

    # Сверка языка по тексту: обученная модель лежит отдельным файлом,
    # и путь к нему библиотека строит от своего __file__, который в
    # сборке подменён. Проверяем не наличие файла, а сам ответ.
    try:
        import py3langid

        русский, _ = py3langid.classify("и в транскрипт попадает всё сказанное")
        чужой, _ = py3langid.classify("si le transcript nu este corect aici")
        _проверить(
            (русский, чужой) == ("ru", "ro"),
            f"сверка языка по тексту работает (ответ: {русский}, {чужой})",
        )
    except Exception as беда:
        _проверить(False, f"сверка языка по тексту упала: {беда}")

    # Показ речи на ходу. Проверяем на самой сборке: в разработке файлы
    # берутся из папки проекта, а в exe — из архива, и разойтись они
    # могут незаметно.
    try:
        from .asr.vad import SpeechSegmenter

        сегментер = SpeechSegmenter(sample_rate=16000)
        _проверить(
            callable(getattr(сегментер, "peek", None)),
            "речь показывается по ходу, а не только после паузы",
        )
    except Exception as беда:
        _проверить(False, f"показ речи на ходу недоступен: {беда}")

    # Разметка и стили попали в сборку целиком: раздел настроек и вид
    # черновика живут там, а не в коде.
    try:
        # Спрашиваем у самой программы, где лежит окно: в сборке файлы
        # уезжают в другое место, и путь «рядом с кодом» тут неверен.
        from .core.paths import web_dir

        разметка = (web_dir() / "index.html").read_text(encoding="utf-8")
        стили = (web_dir() / "styles.css").read_text(encoding="utf-8")
        скрипт = (web_dir() / "app.js").read_text(encoding="utf-8")
        _проверить(
            'data-page="speech"' in разметка and "asr-language" in разметка,
            "раздел настроек распознавания есть в сборке",
        )
        _проверить(
            "turn--draft" in стили and "showDraft" in скрипт,
            "черновик речи отрисуется",
        )

        # Мало уметь рисовать: событие должно доехать до окна. В 0.7.0
        # всё было на месте по отдельности, а список пересылаемых тем
        # про черновик не знал, и людям не показывалось ничего.
        from .core.events import TRANSCRIPT_DRAFT
        from .ui.window import FORWARDED_EVENTS

        _проверить(
            TRANSCRIPT_DRAFT in FORWARDED_EVENTS,
            "черновик доходит до окна, а не теряется по дороге",
        )
    except Exception as беда:
        _проверить(False, f"файлы окна не читаются: {беда}")

    # Настройки, доставшиеся от старых версий. Из-за chunk_seconds = 5.0
    # в файле настроек три выпуска подряд не давали давним пользователям
    # ничего: текст по-прежнему ждал секунды, потому что файл настроек
    # сильнее умолчания в коде.
    try:
        import json
        import tempfile
        from pathlib import Path as _Path

        from .core import paths as _paths
        from .core import settings as _settings

        with tempfile.TemporaryDirectory() as врем:
            файл = _Path(врем) / "settings.json"
            файл.write_text(
                json.dumps({"audio": {"chunk_seconds": 5.0}}), encoding="utf-8"
            )
            было = _paths.settings_path
            _paths.settings_path = lambda: файл  # type: ignore[assignment]
            try:
                с = _settings.load()
            finally:
                _paths.settings_path = было  # type: ignore[assignment]
        _проверить(
            с.audio.chunk_seconds <= 1.0,
            "настройка от старой версии чинится, а не держит задержку",
        )
    except Exception as беда:
        _проверить(False, f"проверка старых настроек не прошла: {беда}")

    # Окно: без него программа запустится и не покажет ничего.
    try:
        import webview  # noqa: F401

        _проверить(True, "движок окна на месте")
    except Exception as беда:
        _проверить(False, f"движок окна не импортируется: {беда}")

    # Диктовка. Её файлы названы по-русски, а PyInstaller ищет модули по
    # именам из импортов: не попади они в сборку, диктовка отвалилась бы
    # только у пользователя и только при попытке ею воспользоваться.
    try:
        from .dictation import вставка as _вставка
        from .dictation.клавиша import КлавишаДиктовки as _Клавиша
        from .dictation.полоска import Полоска as _Полоска  # noqa: F401
        from .dictation.служба import СлужбаДиктовки as _Служба  # noqa: F401

        # Не просто импорт: проверяем, что разбор сочетания работает.
        # Сломайся он — клавиша молча не сработает ни разу.
        мод, кл = _Клавиша("<ctrl>+<shift>+d")._модификаторы, "d"
        _проверить(
            _вставка.ДОСТУПНО and мод == {"ctrl", "shift"},
            "диктовка на месте и разбирает сочетание клавиш",
        )
    except Exception as беда:
        _проверить(False, f"диктовка не собралась: {беда}")

    # Локализация. Словари грузятся не как файлы рядом с кодом, а через
    # тот же путь, которым их берёт окно (paths.web_dir): в сборке он
    # другой, и разойтись может незаметно. Проверяем не наличие файла, а
    # что словарь читается и в нём есть живые переводы: иначе пустой или
    # обрезанный файл пройдёт проверку, а человек увидит голые ключи.
    try:
        import json as _json

        from .core.paths import web_dir as _web_dir

        for язык in ("ru", "en", "es", "sr"):
            путь = _web_dir() / "i18n" / f"{язык}.json"
            словарь = _json.loads(путь.read_text(encoding="utf-8"))
            хорош = bool(
                словарь.get("prefs", {}).get("tab", {}).get("dictation")
                and словарь.get("recording", {}).get("start")
            )
            _проверить(хорош, f"словарь {язык} на месте и переведён")
    except Exception as беда:
        _проверить(False, f"словари интерфейса не читаются: {беда}")

    # Поиск по смыслу. Нарезке текста нужна библиотека tokenizers со
    # своей нативной частью: не попади она в сборку, программа запустится
    # как ни в чём не бывало, поиск по словам будет работать, а по смыслу
    # молча не найдёт ничего. Проверяем не импорт, а саму нарезку: для
    # неё нужен работающий нативный код, а не только файл с питоном.
    try:
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import Whitespace

        from .search import meaning as _смысл  # noqa: F401
        from .search.model import MeaningModel as _Модель  # noqa: F401

        т = Tokenizer(WordLevel({"[UNK]": 0, "оплата": 1, "картой": 2}, unk_token="[UNK]"))
        т.pre_tokenizer = Whitespace()
        _проверить(
            т.encode("оплата картой").ids == [1, 2],
            "поиск по смыслу на месте: нарезка текста работает",
        )
    except Exception as беда:
        _проверить(False, f"поиск по смыслу не собрался: {беда}")

    # Запись по ссылке. yt-dlp находит разборщик сайта по списку имён во
    # время работы, статически этих импортов не видно. Забудь сборка их
    # взять, окно откроется, кнопка будет, а любая ссылка получит
    # «сайт не поддерживается». Сеть не нужна: спрашиваем только, узнаёт
    # ли библиотека адреса.
    try:
        from yt_dlp.extractor import gen_extractor_classes

        from .core import link as _ссылка  # noqa: F401

        адреса = {
            "vk": "https://vk.com/video-1_2",
            "rutube": "https://rutube.ru/video/0123456789abcdef0123456789abcdef/",
            "youtube": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        }
        классы = [к for к in gen_extractor_classes() if к.ie_key() != "Generic"]
        узнаны = [
            сайт for сайт, адрес in адреса.items()
            if any(к.suitable(адрес) for к in классы)
        ]
        _проверить(
            len(узнаны) == len(адреса),
            f"запись по ссылке на месте: сайты узнаются ({', '.join(узнаны) or 'ни один'})",
        )
    except Exception as беда:
        _проверить(False, f"запись по ссылке не собралась: {беда}")

    # Сетевые источники записей: SFTP идёт через paramiko, у которого
    # криптография в отдельных библиотеках. Не доедь они в сборку — окно
    # «Откуда брать записи» откроется, а подключение упадёт у человека.
    try:
        import paramiko  # noqa: F401

        from .core import источники_сеть as _сеть  # noqa: F401

        _проверить(True, f"сетевые источники на месте: paramiko {paramiko.__version__}")
    except Exception as беда:
        _проверить(False, f"сетевые источники не собрались: {беда}")

    # Срок обновлений в ключе «на 3 года» сверяется с датой сборки. Без
    # неё программа взяла бы сегодняшний день, и через три года ключ
    # отказал бы на старой версии, которой он положен навсегда.
    try:
        from app.build_info import BUILD_DATE

        _проверить(bool(BUILD_DATE), f"дата сборки на месте: {BUILD_DATE}")
    except ImportError:
        _проверить(not getattr(sys, "frozen", False),
                   "дата сборки на месте" if not getattr(sys, "frozen", False)
                   else "нет даты сборки: ключ «на 3 года» сверялся бы с часами компьютера")

    # Окно той же сборки, что и питон: иначе человек видит новый номер
    # версии и старое меню (задача №5 в konspekt-releases).
    # В разработке опись остаётся от прошлой сборки, а web/ уже правлен.
    if getattr(sys, "frozen", False):
        try:
            from app.build_info import WEB_FILES

            from .core import paths
            from .core.целостность import расхождения

            беды = расхождения(paths.web_dir(), WEB_FILES)
            _проверить(not беды, f"файлы окна совпадают со сборкой: {len(WEB_FILES)}"
                       if not беды else f"файлы окна не от этой сборки: {'; '.join(беды[:5])}")
        except ImportError:
            _проверить(False, "нет описи файлов окна: недоехавшее обновление не заметить")

    if БЕДЫ:
        СТРОКИ.append(f"Самопроверка не прошла, бед: {len(БЕДЫ)}")
    else:
        СТРОКИ.append("Самопроверка сборки пройдена")

    # Обычная сборка идёт без консоли: печать в никуда. Поэтому отчёт
    # кладём в файл, путь к нему говорит тот, кто запустил проверку.
    отчёт = os.environ.get("KONSPEKT_ОТЧЁТ")
    if отчёт:
        Path(отчёт).write_text("\n".join(СТРОКИ) + "\n", encoding="utf-8")
    else:
        print("\n".join(СТРОКИ), file=sys.stderr)

    return 1 if БЕДЫ else 0

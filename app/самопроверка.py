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

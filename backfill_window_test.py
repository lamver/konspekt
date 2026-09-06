"""Досчёт пропущенной речи глазами пользователя, в настоящем окне.

Отличие от `backfill_ui_test.py`: тот гоняет вырезанный кусок `app.js` в
node с самодельной заглушкой DOM. Заглушка — это моя выдумка о том, как
ведёт себя браузер, и она вполне может врать. Здесь врать нечему:
поднимается настоящий `AppService` с настоящим `MainWindow`, страница
грузится в настоящий WebView2, события идут по настоящей шине через
настоящий мост, а результат читается из настоящего DOM.

Проверяется ровно то, что видит человек после кнопки «стоп»:

1. досчитанная реплика из середины встречи встаёт на своё место, а не в
   конец расшифровки;
2. пока идёт досчёт, в окне видно строку о том, что расшифровка ещё
   дополняется;
3. автоматические заметки не уходят в модель по неполной расшифровке, а
   ждут конца досчёта.

Окно поднимается скрытым: тест не должен мигать перед человеком.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


def main() -> int:
    try:
        import webview  # noqa: F401
    except ImportError:
        # Окно есть не в каждой среде (сборочный агент без WebView2).
        # Молчать нельзя: иначе пропажу проверки заметят не сразу.
        print("[пропуск] pywebview не установлен, настоящее окно не проверено")
        return 0

    import testenv  # noqa: F401  русский вывод в консоли Windows

    tmp = Path(tempfile.mkdtemp(prefix="konspekt-backfill-live-"))
    from app.core import paths as paths_mod

    audio = tmp / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    paths_mod.data_dir = lambda: tmp  # type: ignore[assignment]
    paths_mod.audio_dir = lambda: audio  # type: ignore[assignment]
    paths_mod.db_path = lambda: tmp / "konspekt.db"  # type: ignore[assignment]
    paths_mod.models_dir = lambda: tmp / "models"  # type: ignore[assignment]
    paths_mod.settings_path = lambda: tmp / "settings.json"  # type: ignore[assignment]

    import webview
    from app.audio import NullCapture
    from app.core.events import (
        RECOGNITION_BACKFILL, RECORDING_STOPPED, TRANSCRIPT_SEGMENT, bus,
    )
    from app.core.service import AppService
    from app.storage.db import Store
    from app.ui.window import MainWindow

    service = AppService(store=Store(str(tmp / "konspekt.db")),
                         capture=NullCapture(), transcriber=None)
    # Окно поднимаем скрытым: тест не должен мигать перед человеком.
    # Флаг читается внутри create(), поэтому ставим его до вызова.
    service.settings.start_hidden = True
    окно = MainWindow(service)
    w = окно.create()

    итог: dict[str, object] = {}

    def сцена() -> None:
        try:
            _сцена(w, service, bus, RECOGNITION_BACKFILL,
                   RECORDING_STOPPED, TRANSCRIPT_SEGMENT, итог)
        except Exception as e:  # noqa: BLE001
            итог["ошибка"] = repr(e)
        finally:
            # Именно quit(), а не destroy(): крестик в Konspekt прячет окно в
            # трей, и без флага выхода цикл сообщений не завершается.
            try:
                окно.quit()
            except Exception:
                pass

    def сторож() -> None:
        """Закрыть окно, если сцена где-то зависла.

        Без этого webview.start() держит главный поток вечно, и тест
        висит молча — хуже, чем честно упасть.
        """
        for _ in range(120):
            time.sleep(1)
            if "готово" in итог or "ошибка" in итог:
                return
        итог.setdefault("ошибка", "сцена не успела за 120с")
        try:
            окно.quit()
        except Exception:
            pass

    threading.Thread(target=сцена, daemon=True).start()
    threading.Thread(target=сторож, daemon=True).start()
    # start() держит поток до закрытия окна: это цикл сообщений Windows,
    # и жить он обязан в главном потоке.
    webview.start()
    service.shutdown()

    if "ошибка" in итог:
        проверить(False, f"сцена отработала: {итог['ошибка']}")
    else:
        проверить(итог.get("порядок") == ["первая", "досчитанная", "последняя"],
                  f"досчитанная реплика встала на своё место по времени "
                  f"(в окне: {итог.get('порядок')})")
        подсказка = str(итог.get("текст_подсказки") or "")
        проверить(bool(итог.get("подсказка_видна"))
                  and "Досчитываем" in подсказка,
                  f"пока идёт досчёт, человек видит строку именно о нём "
                  f"({подсказка!r})")
        проверить(bool(итог.get("подсказка_ушла")),
                  "по концу досчёта строка о нём убирается")
        проверить(итог.get("заметки_до") is False,
                  "заметки не ушли в модель по неполной расшифровке")
        проверить(итог.get("заметки_после") is True,
                  "по концу досчёта отложенные заметки всё-таки запускаются")

    if БЕДЫ:
        print(f"\nПровалено проверок: {len(БЕДЫ)}")
        return 1
    print("\nВ настоящем окне досчёт виден, порядок реплик верный, "
          "заметки ждут полной расшифровки")
    return 0


def _дождаться(w, выражение: str, секунд: float = 10.0) -> bool:
    """Ждать, пока условие в окне станет истинным.

    Фиксированный sleep здесь не годится: событие идёт через мост в
    браузер, и под нагрузкой (общий прогон тестов) оно доезжает
    позже, чем на пустой машине. От этого тест моргал красным на
    ровном коде, а мигающий тест хуже, чем никакого.
    """
    срок = time.time() + секунд
    while time.time() < срок:
        try:
            if w.evaluate_js(выражение):
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def _сцена(w, service, bus, RECOGNITION_BACKFILL,
           RECORDING_STOPPED, TRANSCRIPT_SEGMENT, итог) -> None:
    # Ждём, пока страница поднимется и объявит свои функции.
    for _ in range(100):
        time.sleep(0.1)
        try:
            if w.evaluate_js("typeof window.__konspekt_event") == "function":
                break
        except Exception:
            continue
    else:
        raise RuntimeError("страница так и не поднялась")
    print("[шаг] страница поднялась", flush=True)

    встреча = service.create_meeting("Проба досчёта")
    ид = встреча["id"]

    # Открываем встречу так же, как это делает человек кликом по списку.
    w.evaluate_js(f"selectMeeting({json.dumps(ид)})")
    if not _дождаться(w, f"state.currentId === {json.dumps(ид)}"):
        raise RuntimeError("окно так и не открыло встречу")
    # Заметки подменяем счётчиком: настоящая модель весит полтора
    # гигабайта, а проверяется здесь момент запуска, а не её ответ.
    w.evaluate_js("window.__заметок = 0;"
                  "window.maybeAutoSummary = function () { window.__заметок += 1; };")
    w.evaluate_js("state.isRecording = true; state.recordingId = "
                  + json.dumps(ид) + ";")

    def реплика(текст: str, старт: float, конец: float, голос: str) -> None:
        было = w.evaluate_js("ui.transcript.children.length")
        bus.emit(TRANSCRIPT_SEGMENT, {"segment": {
            "id": f"{текст}-{старт}", "meeting_id": ид, "speaker": "me",
            "text": текст, "start": старт, "end": конец, "lang": "ru",
            "voice_id": голос, "voice_label": "Я",
        }})
        if not _дождаться(w, f"ui.transcript.children.length > {было}"):
            raise RuntimeError(f"реплика {текст!r} так и не появилась в окне")

    # Живая встреча: две реплики по краям, между ними дыра — тот самый
    # кусок, который не влез в очередь распознавания при перегрузке.
    реплика("первая", 0.0, 2.0, "a")
    реплика("последняя", 30.0, 32.0, "b")

    # Кнопка «стоп»: часть речи ещё досчитывается из записи.
    bus.emit(RECORDING_STOPPED, {"meeting_id": ид, "backfill": True})
    # Заметки откладываются мгновенно, но само событие ещё идёт через
    # мост. Ждём его прихода по признаку «запись больше не идёт», иначе
    # проверка читала бы состояние до обработки события.
    _дождаться(w, "state.isRecording === false")
    итог["заметки_до"] = w.evaluate_js("window.__заметок > 0")

    bus.emit(RECOGNITION_BACKFILL,
             {"meeting_id": ид, "state": "start", "total": 1})
    # Перед досчётом в том же месте могло висеть чужое сообщение
    # (например, о загрузке модели), поэтому ждём именно свою строку,
    # а не просто видимого уведомления.
    _дождаться(w, "!!(ui.toast && ui.toast.hidden === false && ui.toastText"
                   " && ui.toastText.textContent.indexOf('Досчитываем') >= 0)")
    итог["подсказка_видна"] = w.evaluate_js(
        "!!(ui.toast && ui.toast.hidden === false)")
    итог["текст_подсказки"] = w.evaluate_js(
        "ui.toastText ? ui.toastText.textContent : ''")

    # Досчитанный кусок из середины встречи.
    реплика("досчитанная", 15.0, 17.0, "c")

    bus.emit(RECOGNITION_BACKFILL,
             {"meeting_id": ид, "state": "done", "total": 1})
    _дождаться(w, "!!(ui.toast && ui.toast.hidden === true)")
    _дождаться(w, "window.__заметок > 0")

    итог["подсказка_ушла"] = w.evaluate_js(
        "!!(ui.toast && ui.toast.hidden === true)")
    итог["заметки_после"] = w.evaluate_js("window.__заметок > 0")
    итог["порядок"] = w.evaluate_js(
        "Array.from(ui.transcript.querySelectorAll('.turn__text'))"
        ".map(function (n) { return n.textContent; })")
    итог["готово"] = True


if __name__ == "__main__":
    sys.exit(main())

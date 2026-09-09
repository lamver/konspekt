"""Снимки экрана для страницы проекта.

Отдельный инструмент, а не «сфотографировал своё окно», по одной
причине: на своём окне лежат настоящие встречи, имена коллег и куски
разговоров. Такие снимки нельзя ни показать миру, ни аккуратно
почистить — что-нибудь всё равно останется в углу списка.

Поэтому программа поднимается на отдельном профиле, в пустой временной
папке, и наполняется выдуманной встречей: вымышленная команда,
вымышленный проект. Личным данным взяться неоткуда физически, а не
потому, что мы старались их не заснять.

Снимает четыре экрана на четырёх языках:

    python tools/снимки.py

Складывает в docs/screenshots/<язык>/<экран>.png. Профиль под каждый
язык свой и удаляется следом.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

# До импорта app: сервис читает переменную при запуске. Без неё программа
# честно полезет качать 220 МБ модели в нашу временную папку — долго,
# бессмысленно и лезет полосой загрузки в кадр.
os.environ["KONSPEKT_NO_PREFETCH"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import testenv  # noqa: E402,F401  русский вывод в консоли Windows

from tools.встреча_для_снимков import ВСТРЕЧИ  # noqa: E402
from tools.снимок_окна import снять_окно  # noqa: E402

КОРЕНЬ = Path(__file__).resolve().parent.parent
КУДА = КОРЕНЬ / "docs" / "screenshots"
ЯЗЫКИ = ("en", "es", "sr", "ru")

# Размер окна на снимке. Больше, чем окно по умолчанию: на странице
# картинка ужимается, и на узком окне текст превратится в кашу.
ШИРИНА, ВЫСОТА = 1060, 760


def _наполнить(service, язык: str) -> str:
    """Создать выдуманную встречу на нужном языке и вернуть её id."""
    from app.core.models import Speaker, TranscriptSegment

    данные = ВСТРЕЧИ[язык]
    сейчас = time.time()

    # Соседние встречи в списке: с одной-единственной строкой окно
    # выглядит пустым, будто программой никто не пользуется. Им нужны
    # только название и дата, внутрь никто не заглянет.
    #
    # Дату правим прямо в базе: в списке показывается created_at, а его
    # update_meeting осознанно не трогает — время создания встречи не
    # должно меняться по ходу работы. Здесь же нам нужно ровно оно,
    # иначе все встречи выйдут сегодняшними и список будет выглядеть
    # так, будто человек провёл четыре планёрки за одно утро.
    for сдвиг, имя in enumerate(данные["соседние"], start=1):  # type: ignore[arg-type]
        соседняя = service.create_meeting(имя)
        начало = сейчас - сдвиг * 86400
        service.store.update_meeting(
            соседняя["id"], status="ready",
            started_at=начало, ended_at=начало + 1800,
        )
        with service.store._lock:
            service.store._conn.execute(
                "UPDATE meetings SET created_at=? WHERE id=?",
                (начало, соседняя["id"]),
            )
            service.store._conn.commit()

    встреча = service.create_meeting(данные["название"])  # type: ignore[arg-type]
    ид = встреча["id"]

    голоса: dict[str, str] = {}
    for дорожка, имя, текст, старт, конец in данные["реплики"]:  # type: ignore[union-attr]
        голоса.setdefault(имя, f"voice-{len(голоса) + 1}")
        service.store.add_segment(TranscriptSegment(
            meeting_id=ид,
            speaker=Speaker.ME if дорожка == "me" else Speaker.THEM,
            text=текст,
            start=старт,
            end=конец,
            lang=язык,
            voice_id=голоса[имя],
            voice_label=имя,
        ))

    начало = сейчас - 3600
    service.store.update_meeting(
        ид,
        notes=данные["заметки"],
        summary=данные["саммари"],
        status="ready",
        started_at=начало,
        ended_at=начало + 1500,  # 25 минут: правдоподобная планёрка
    )
    return ид


# --- Съёмка ----------------------------------------------------------------


def _ждать(w, условие: str, секунд: float = 30.0) -> bool:
    предел = time.time() + секунд
    while time.time() < предел:
        try:
            if w.evaluate_js(условие):
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _снять(w, язык: str, имя: str) -> None:
    # Гасим всё, что относится к первому запуску, а не к программе:
    # полосу «качаем модель», всплывающее сообщение и оповещение о новой
    # версии. На снимке они говорят о нашей временной папке, а читатель
    # решит, что программа вечно что-то скачивает.
    #
    # Перед каждым кадром, а не один раз: между снимками проходят
    # секунды, и фоновое событие успевает показаться снова.
    w.evaluate_js(
        "if (typeof hideModelLoad === 'function') hideModelLoad();"
        "if (ui.toast) ui.toast.hidden = true;"
        "if (ui.updateNote) ui.updateNote.hidden = true;"
    )
    time.sleep(0.4)

    папка = КУДА / язык
    папка.mkdir(parents=True, exist_ok=True)
    снять_окно("Konspekt", папка / f"{имя}.png")
    print(f"  [снято] {язык}/{имя}.png")


def _сцена(w, service, язык: str, ид: str, итог: dict) -> None:
    if not _ждать(w, "typeof showPrefsTab === 'function'"
                     " && window.__konspekt_i18n_ready === true", 120):
        raise RuntimeError("страница так и не поднялась")

    w.evaluate_js(f"setLanguage({json.dumps(язык)})")
    if not _ждать(w, f"i18n.lang === {json.dumps(язык)}"):
        raise RuntimeError(f"язык {язык} не применился")
    time.sleep(1.2)

    w.evaluate_js(f"selectMeeting({json.dumps(ид)})")
    if not _ждать(w, f"state.currentId === {json.dumps(ид)}"):
        raise RuntimeError("встреча не открылась")
    time.sleep(0.8)

    # 1. Саммари: главное обещание программы, поэтому первый кадр.
    w.evaluate_js("showTab('summary')")
    time.sleep(1.0)
    _снять(w, язык, "summary")

    # 2. Транскрипт с именами говорящих.
    w.evaluate_js("showTab('transcript')")
    time.sleep(1.0)
    _снять(w, язык, "transcript")

    # 3. Свои заметки рядом с расшифровкой.
    w.evaluate_js("showTab('notes')")
    time.sleep(1.0)
    _снять(w, язык, "notes")

    # 4. Настройки диктовки: новая возможность 0.8.0.
    # Панель настроек показывается своим флагом, а showPrefsTab
    # только переключает раздел внутри неё.
    w.evaluate_js("ui.audioSheet.hidden = false; showPrefsTab('dictation')")
    time.sleep(1.2)
    _снять(w, язык, "dictation")

    итог["готово"] = True


def _снять_язык(язык: str) -> None:
    """Поднять окно на чистом профиле и снять все кадры одного языка."""
    tmp = Path(tempfile.mkdtemp(prefix=f"konspekt-снимки-{язык}-"))

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
    from app.core.service import AppService
    from app.storage.db import Store
    from app.ui.window import MainWindow

    service = AppService(store=Store(str(tmp / "konspekt.db")),
                         capture=NullCapture(), transcriber=None)
    # Проверка обновлений на снимке ни к чему: полоска «вышла новая
    # версия» переезжает поверх окна и портит кадр.
    service.settings.check_updates = False
    service.settings.window.width = ШИРИНА
    service.settings.window.height = ВЫСОТА
    service.settings.always_on_top = False

    ид = _наполнить(service, язык)

    окно = MainWindow(service)
    w = окно.create()

    итог: dict[str, object] = {}

    def сцена() -> None:
        try:
            _сцена(w, service, язык, ид, итог)
        except Exception as e:  # noqa: BLE001
            итог["ошибка"] = repr(e)
        finally:
            try:
                окно.quit()
            except Exception:
                pass

    threading.Thread(target=сцена, daemon=True).start()
    # start() держит поток до закрытия окна: это цикл сообщений Windows,
    # и жить он обязан в главном потоке.
    webview.start()
    service.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

    if "ошибка" in итог:
        raise RuntimeError(f"{язык}: {итог['ошибка']}")


def main() -> int:
    if sys.platform != "win32":
        print("Снимки делаются под Windows: там живёт само окно.")
        return 1

    for язык in ЯЗЫКИ:
        print(f"[язык] {язык}")
        try:
            _снять_язык(язык)
        except Exception as e:  # noqa: BLE001
            print(f"Не сняли: {e}")
            return 1

    print(f"\nГотово. Снимки в {КУДА}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

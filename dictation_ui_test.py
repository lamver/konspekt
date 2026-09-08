"""Раздел диктовки глазами человека, в настоящем окне.

Заглушкой DOM тут не обойтись, и это не вкусовщина. На этапе 26 уже
случилось ровно то, от чего страхует эта проверка: двадцать шесть
зелёных проверок при полностью нерабочей программе, потому что все они
звали функции напрямую, минуя окно. Одна строка (событие не попало в
список пересылаемых) — и вся работа этапа не видна человеку.

Поэтому здесь врать нечему: поднимается настоящий `AppService` с
настоящим `MainWindow`, страница грузится в настоящий WebView2, вкладка
открывается тем же кодом, что и по щелчку человека, а состояние
переключателей читается из настоящего DOM.

Окно поднимается скрытым: проверка не должна мигать перед человеком.
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


def _сцена(w, service, итог: dict) -> None:
    """Всё то же, что делает человек: открыть раздел и пощёлкать."""
    # Страница грузится не мгновенно, а звать её код раньше времени
    # значит получить пустоту и решить, что раздела нет.
    for _ in range(60):
        try:
            if w.evaluate_js(
                "typeof loadDictation === 'function' && window.__konspekt_i18n_ready === true"
            ):
                break
        except Exception:
            pass
        time.sleep(0.5)

    проверки: dict[str, object] = {}

    # Открываем раздел ровно тем же вызовом, что и щелчок по вкладке.
    w.evaluate_js("showPrefsTab('dictation')")
    time.sleep(1.0)

    проверки["вкладка_есть"] = w.evaluate_js(
        "!!document.querySelector('.prefs__tab[data-tab=\"dictation\"]')"
    )
    проверки["страница_видна"] = w.evaluate_js(
        "!document.querySelector('.prefs__page[data-page=\"dictation\"]').hidden"
    )
    проверки["заголовок"] = w.evaluate_js(
        "document.getElementById('prefs-title').textContent"
    )
    # Выключено по умолчанию: диктовка перехватывает клавиши глобально.
    проверки["сначала_выключена"] = w.evaluate_js(
        "document.getElementById('dictation-enabled').checked"
    )

    # Человек ставит галочку. Именно событием, а не вызовом функции:
    # иначе мы проверим свой код в обход того, что делает браузер.
    w.evaluate_js(
        "(() => { const c = document.getElementById('dictation-enabled');"
        " c.checked = true;"
        " c.dispatchEvent(new Event('change')); })()"
    )
    # Сохранение идёт через мост, ему нужно время долететь до питона.
    for _ in range(40):
        time.sleep(0.25)
        if service.settings.dictation.enabled:
            break

    проверки["галочка_дошла"] = service.settings.dictation.enabled
    проверки["клавиша_слушается"] = service._клавиша_диктовки is not None

    # Каждый список проверяем отдельно. Вместе нельзя: обработчик
    # сохраняет разом все поля формы, поэтому один живой список
    # донёс бы и значение соседнего, у которого обработчик отвалился.
    w.evaluate_js(
        "(() => { const m = document.getElementById('dictation-mode');"
        " m.value = 'toggle'; m.dispatchEvent(new Event('change')); })()"
    )
    for _ in range(40):
        time.sleep(0.25)
        if service.settings.dictation.mode == "toggle":
            break
    проверки["режим_дошёл"] = service.settings.dictation.mode

    w.evaluate_js(
        "(() => { const h = document.getElementById('dictation-hotkey');"
        " h.value = '<ctrl>+<alt>+d'; h.dispatchEvent(new Event('change')); })()"
    )
    for _ in range(40):
        time.sleep(0.25)
        if service.settings.dictation.hotkey == "<ctrl>+<alt>+d":
            break
    проверки["клавиша_дошла"] = service.settings.dictation.hotkey

    w.evaluate_js(
        "(() => { const p = document.getElementById('dictation-paste');"
        " p.value = 'type'; p.dispatchEvent(new Event('change')); })()"
    )
    for _ in range(40):
        time.sleep(0.25)
        if service.settings.dictation.paste_method == "type":
            break
    проверки["способ_дошёл"] = service.settings.dictation.paste_method

    # Снимаем галочку: перехват обязан сняться сразу.
    w.evaluate_js(
        "(() => { const c = document.getElementById('dictation-enabled');"
        " c.checked = false; c.dispatchEvent(new Event('change')); })()"
    )
    for _ in range(40):
        time.sleep(0.25)
        if not service.settings.dictation.enabled:
            break
    проверки["выключение_дошло"] = not service.settings.dictation.enabled
    проверки["перехват_снят"] = service._клавиша_диктовки is None

    итог.update(проверки)
    итог["готово"] = True


def main() -> int:
    if sys.platform != "win32":
        print("[пропуск] окно проверяется только под Windows")
        return 0
    try:
        import webview  # noqa: F401
    except ImportError:
        # Окно есть не в каждой среде (сборочный агент без WebView2).
        # Молчать нельзя: иначе пропажу проверки заметят не сразу.
        print("[пропуск] pywebview не установлен, настоящее окно не проверено")
        return 0

    import testenv  # noqa: F401  русский вывод в консоли Windows

    tmp = Path(tempfile.mkdtemp(prefix="konspekt-диктовка-окно-"))
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
    service.settings.start_hidden = True
    окно = MainWindow(service)
    w = окно.create()

    итог: dict[str, object] = {}

    def сцена() -> None:
        try:
            _сцена(w, service, итог)
        except Exception as e:  # noqa: BLE001
            итог["ошибка"] = repr(e)
        finally:
            # Именно quit(), а не destroy(): крестик прячет окно в трей,
            # и без флага выхода цикл сообщений не завершается.
            try:
                окно.quit()
            except Exception:
                pass

    def сторож() -> None:
        """Закрыть окно, если сцена зависла: молчаливое зависание хуже падения."""
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
        проверить(bool(итог.get("вкладка_есть")),
                  "вкладка «Диктовка» есть в настройках")
        проверить(bool(итог.get("страница_видна")),
                  "по щелчку раздел открывается, а не остаётся пустым")
        проверить(итог.get("заголовок") == "Диктовка",
                  f"заголовок раздела верный (было {итог.get('заголовок')!r})")
        проверить(итог.get("сначала_выключена") is False,
                  "диктовка выключена по умолчанию: она перехватывает "
                  "клавиши, включать такое без спроса нельзя")
        проверить(bool(итог.get("галочка_дошла")),
                  "галочка в окне доходит до настроек программы")
        проверить(bool(итог.get("клавиша_слушается")),
                  "после галочки клавиша правда перехватывается, а не "
                  "только записывается в файл")
        проверить(итог.get("режим_дошёл") == "toggle",
                  f"смена режима доходит (в настройках {итог.get('режим_дошёл')!r})")
        проверить(итог.get("клавиша_дошла") == "<ctrl>+<alt>+d",
                  f"смена сочетания доходит (в настройках "
                  f"{итог.get('клавиша_дошла')!r})")
        проверить(итог.get("способ_дошёл") == "type",
                  f"смена способа вставки доходит (в настройках "
                  f"{итог.get('способ_дошёл')!r})")
        проверить(bool(итог.get("выключение_дошло")),
                  "снятая галочка тоже доходит")
        проверить(bool(итог.get("перехват_снят")),
                  "по снятии галочки перехват клавиш снимается сразу")

    if БЕДЫ:
        print(f"\nНе прошло проверок: {len(БЕДЫ)}")
        return 1
    print("\nВсе проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

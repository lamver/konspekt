"""Снимки для страницы-инструкции Конспекта на сайтах: все экраны, четыре языка.

Родственник tools/снимки.py: тот снимает четыре кадра для страницы
проекта на GitHub, этот — полтора десятка кадров для страницы «что умеет
и как быстро настроить». Окно поднимается так же: на временном профиле,
наполненном выдуманными встречами (tools/встреча_для_снимков.py и
tools/инструкция_данные.py), поэтому настоящим данным в кадр попасть
неоткуда.

То, что на временном профиле не воспроизвести честно, подставляется
прямо в страницу: идущая запись (нужен микрофон), подключённый бот
Telegram (нужен настоящий токен), скачанные модели заметок (полтора
гигабайта). Страница рисует их теми же функциями, что и в работе,
подменяются только ответы моста.

    python tools/снимки_инструкции.py            # все языки
    python tools/снимки_инструкции.py ru en      # выбранные

Складывает в docs/screenshots/<язык>/NN-имя.png.
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

os.environ["KONSPEKT_NO_PREFETCH"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import testenv  # noqa: E402,F401  русский вывод в консоли Windows

from tools.инструкция_данные import ДАННЫЕ  # noqa: E402
from tools.снимки import ВЫСОТА, ШИРИНА, КУДА, _ждать, _наполнить  # noqa: E402
from tools.снимок_окна import снять_страницу  # noqa: E402

ЯЗЫКИ = ("ru", "en", "es", "sr")

# Кнопка «переслушать» видна только при наведении мыши, а мышь в кадр
# не позовёшь. Помечаем одну реплику, и она выглядит как под курсором.
СТИЛЬ = """
.turn.снимок-курсор .turn__play { opacity: 1; color: var(--accent); background: var(--paper-3); }
.turn.снимок-курсор .turn__lang { opacity: .7; }
"""


def _js(значение: object) -> str:
    return json.dumps(значение, ensure_ascii=False)


def _наполнить_инструкцию(service, язык: str, ид_планёрки: str) -> dict[str, str]:
    """Звонок клиенту, вопросы к планёрке, папки, идущая запись."""
    from app.core.models import ChatMessage, Speaker, TranscriptSegment

    д = ДАННЫЕ[язык]
    store = service.store
    сейчас = time.time()

    def реплики(ид: str, список) -> None:
        голоса: dict[str, str] = {}
        for дорожка, имя, текст, старт, конец in список:
            голоса.setdefault(имя, f"voice-{len(голоса) + 1}")
            store.add_segment(TranscriptSegment(
                meeting_id=ид, speaker=Speaker.ME if дорожка == "me" else Speaker.THEM,
                text=текст, start=старт, end=конец, lang=язык,
                voice_id=голоса[имя], voice_label=имя,
            ))

    # Звонок клиенту: для разбора по SPIN, «Разбора разговора» и личных данных.
    звонок = д["звонок"]
    ид_звонка = service.create_meeting(звонок["название"])["id"]
    реплики(ид_звонка, звонок["реплики"])
    начало = сейчас - 2 * 3600
    store.update_meeting(ид_звонка, status="ready", started_at=начало, ended_at=начало + 70,
                         summary=звонок["итоги"])
    store.save_analysis(ид_звонка, "sales", звонок["спин"],
                        store.meaning_signature(ид_звонка), "local:smart")

    # Кнопка «переслушать» есть только у встреч со звуком. Сам звук в кадре
    # не нужен: хватит отметки, что запись есть.
    for ид in (ид_планёрки, ид_звонка):
        store.add_audio_chunk(ид, "me", "нет-файла.wav", 0.0, 70.0)

    # Вопрос к планёрке и ответ модели.
    store.add_chat_message(ChatMessage(meeting_id=ид_планёрки, role="user", text=д["вопрос"]))
    store.add_chat_message(ChatMessage(meeting_id=ид_планёрки, role="assistant", text=д["ответ"]))

    # Встреча, на которой «идёт запись».
    запись = д["запись"]
    ид_записи = service.create_meeting(запись["название"])["id"]
    реплики(ид_записи, запись["реплики"])
    store.update_meeting(ид_записи, status="recording", started_at=сейчас - 754)

    # Папки: команда и звонки, которые сами приходят с АТС по SFTP.
    команда = service.create_folder(д["папка_команда"])["id"]
    продажи = service.create_folder(д["папка_продажи"])["id"]
    for m in store.list_meetings():
        if m.id in (ид_звонка, ид_записи):
            continue
        store.set_meeting_folder(m.id, команда)
    store.set_meeting_folder(ид_звонка, продажи)

    from app.core import источники_сеть as сеть

    сфтп = dict(д["сфтп"])
    store.set_folder_source(
        продажи, сеть.описание("sftp", сфтп), "sftp",
        json.dumps({**сфтп, "password": "dpapi:выдумано", "since": сейчас}, ensure_ascii=False),
    )
    return {"звонок": ид_звонка, "запись": ид_записи, "продажи": продажи}


def _подменить(w, метод: str, ответ: object) -> None:
    """Ответ моста на время съёмки: страница вызовет метод и получит это."""
    w.evaluate_js(f"window.pywebview.api[{_js(метод)}] = async () => ({_js(ответ)});")


def _чисто(w) -> None:
    """Убрать всё, что могло остаться от прошлого кадра."""
    w.evaluate_js(
        "if (typeof hideModelLoad === 'function') hideModelLoad();"
        "if (ui.toast) ui.toast.hidden = true;"
        "if (ui.updateNote) ui.updateNote.hidden = true;"
        "if (ui.modelBar) ui.modelBar.hidden = true;"
        "ui.audioSheet.hidden = true;"
        "el('source-sheet').hidden = true; el('link-sheet').hidden = true;"
        "hideNoticedTalkAsk(); closeCtxMenu();"
        "state.licenseBarHidden = true; renderLicense();"
    )


def _снять(w, язык: str, имя: str, чистить: bool = False) -> None:
    if чистить:
        _чисто(w)
    time.sleep(0.6)
    снять_страницу(w, КУДА / язык / f"{имя}.png")
    print(f"  [снято] {язык}/{имя}.png")


def _открыть(w, ид: str) -> None:
    w.evaluate_js(f"selectMeeting({_js(ид)})")
    if not _ждать(w, f"state.currentId === {_js(ид)}"):
        raise RuntimeError("встреча не открылась")
    time.sleep(0.8)


def _поиск(w, текст: str) -> None:
    w.evaluate_js(
        f"ui.search.value = {_js(текст)};"
        "ui.search.dispatchEvent(new Event('input', { bubbles: true }));"
    )
    time.sleep(1.2)


def _сцена(w, service, язык: str, ид: dict[str, str], llm: dict, итог: dict) -> None:
    д = ДАННЫЕ[язык]
    исходный = w.evaluate_js

    def с_подсказкой(код: str, *a, **k):
        try:
            return исходный(код, *a, **k)
        except Exception as e:
            raise RuntimeError(f"{e!r} в коде: {код[:160]}") from e

    w.evaluate_js = с_подсказкой
    if not _ждать(w, "typeof showPrefsTab === 'function'"
                     " && window.__konspekt_i18n_ready === true", 120):
        raise RuntimeError("страница так и не поднялась")

    w.evaluate_js(f"setLanguage({_js(язык)})")
    if not _ждать(w, f"i18n.lang === {_js(язык)}"):
        raise RuntimeError(f"язык {язык} не применился")
    тема_была = w.evaluate_js("state.theme")
    w.evaluate_js(
        f"(() => {{ const s = document.createElement('style'); s.textContent = {_js(" ".join(СТИЛЬ.split()))};"
        " document.head.appendChild(s); })()"
    )
    w.evaluate_js("applyTheme('light')")
    # Модели заметок на временном профиле не скачаны; показываем так,
    # будто скачаны: иначе в каждом кадре висит «модель не загружена».
    _подменить(w, "llm_status", llm)
    w.evaluate_js(f"renderLlmSettings({_js(llm)})")
    time.sleep(1.0)

    # 12. Первый запуск: встреч ещё нет, качается модель распознавания.
    _чисто(w)
    w.evaluate_js(
        "state.currentId = null; state.current = null;"
        "ui.list.innerHTML = '';"
        "toggleEmptyState();"
        "onModelProgress({ state: 'downloading', bytes: 96e6, total: 220e6 });"
    )
    time.sleep(0.8)
    _снять(w, язык, "17-pervyj-zapusk")
    w.evaluate_js("state.modelToastShown = false; hideModelLoad(); hideToast(); renderMeetingList();")

    # 1. Идёт запись: две дорожки, волна, текст появляется по ходу.
    _открыть(w, ид["запись"])
    w.evaluate_js("showTab('transcript')")
    w.evaluate_js(
        "window.__konspekt_event({ topic: 'recording.started', meeting_id: state.currentId,"
        f" started_at: {time.time() - 754} }});"
        "(() => { for (let i = 0; i < 220; i++) {"
        "  const говорит = Math.floor(i / 40) % 2;"
        "  const речь = () => 0.05 + Math.abs(Math.sin(i * 0.9) * Math.cos(i * 0.37)) * 0.55;"
        "  renderLevels(говорит ? речь() * 0.15 : речь(), говорит ? речь() : 0.01);"
        "} })();"
        f"showDraft({{ speaker: 'them', text: {_js(д['запись']['черновик'])} }});"
    )
    _снять(w, язык, "01-zapis", чистить=True)
    w.evaluate_js(
        "clearDraft(); state.isRecording = false; state.recordingId = null;"
        "stopTimer(); renderRecordingState();"
    )

    # 3. Расшифровка с говорящими и кнопкой «переслушать».
    _открыть(w, ид["планёрка"])
    w.evaluate_js("showTab('transcript')")
    time.sleep(0.6)
    w.evaluate_js(
        "(() => { const t = ui.transcript.querySelectorAll('.turn'); if (t[2]) t[2].classList.add('снимок-курсор'); })()"
    )
    _снять(w, язык, "03-rasshifrovka", чистить=True)

    # 4. Итоги встречи.
    w.evaluate_js("showTab('summary'); applySummaryCollapsed(false); openLens('summary')")
    _снять(w, язык, "04-itogi", чистить=True)

    # 14. То же в тёмной теме: для первого экрана страницы.
    w.evaluate_js("applyTheme('dark')")
    _снять(w, язык, "20-temnaja-tema", чистить=True)
    w.evaluate_js("applyTheme('light')")

    # 5. Чат по встрече. Итоги свёрнуты: так переписка видна целиком.
    w.evaluate_js("applySummaryCollapsed(true)")
    _снять(w, язык, "05-chat", чистить=True)
    w.evaluate_js("applySummaryCollapsed(false)")

    # 6. Разбор звонка по SPIN.
    _открыть(w, ид["звонок"])
    w.evaluate_js("showTab('summary')")
    time.sleep(0.8)
    _снять(w, язык, "06-razbory", чистить=True)
    w.evaluate_js("openLens('sales')")
    _снять(w, язык, "07-spin", чистить=True)

    # 7. Разбор разговора: кто сколько говорил.
    w.evaluate_js("openLens('talk')")
    _снять(w, язык, "08-razbor-razgovora", чистить=True)

    # 8. Личные данные в расшифровке звонка.
    w.evaluate_js("showTab('transcript')")
    time.sleep(0.6)
    w.evaluate_js("togglePersonal()")
    time.sleep(1.0)
    _снять(w, язык, "10-lichnye-dannye", чистить=True)
    w.evaluate_js("if (state.personalShown) togglePersonal()")

    # 2. Предложение записать замеченный разговор.
    w.evaluate_js("showTab('summary'); backToLenses()")
    _чисто(w)
    w.evaluate_js("showNoticedTalkAsk({ 'секунд': 30 })")
    _снять(w, язык, "02-uvedomlenie")
    w.evaluate_js("hideNoticedTalkAsk()")

    # 7. Поиск по всем встречам и папки.
    _открыть(w, ид["планёрка"])
    w.evaluate_js("showTab('transcript')")
    _поиск(w, д["поиск"])
    _снять(w, язык, "09-poisk-i-papki", чистить=True)
    _поиск(w, "")

    # 9. Источники: ссылка и папка-источник.
    _чисто(w)
    w.evaluate_js(f"openLinkSheet({_js(д['ссылка'])})")
    _снять(w, язык, "11-istochnik-ssylka")
    w.evaluate_js("closeLinkSheet()")

    сфтп = д["сфтп"]
    _чисто(w)
    w.evaluate_js(
        f"openSourceSheet({{ id: {_js(ид['продажи'])}, name: '' }}).then(() => {{"
        " el('source-kind').value = 'sftp'; syncSourceKind();"
        f" el('source-host').value = {_js(сфтп['host'])}; el('source-port').value = {_js(сфтп['port'])};"
        f" el('source-user').value = {_js(сфтп['user'])}; el('source-password').value = 'выдуманный-пароль';"
        f" el('source-dir').value = {_js(сфтп['dir'])};"
        " el('source-take').value = 'new'; })"
    )
    _снять(w, язык, "12-istochnik-ats-sftp")

    w.evaluate_js(
        "el('source-kind').value = 'mango'; syncSourceKind();"
        " el('source-mango-key').value = 'выдуманный-код'; el('source-mango-salt').value = 'выдуманный-ключ';"
        " el('source-result').hidden = true;"
    )
    _снять(w, язык, "13-istochnik-mango")

    w.evaluate_js(
        "el('source-kind').value = 'bitrix'; syncSourceKind();"
        " el('source-bitrix-webhook').value = 'https://example.bitrix24.ru/rest/1/vydumano/';"
    )
    _снять(w, язык, "14-istochnik-bitrix24")
    w.evaluate_js("closeSourceSheet()")

    # 10. Свой бот в Telegram, уже привязанный, с выбором людей.
    тг = д["телеграм"]
    _подменить(w, "telegram_state", {
        "has_token": True, "enabled": True, "linked": True,
        "bot_name": тг["бот"], "chat_name": тг["хозяин"], "access": "chosen",
        "users": [
            {"id": i + 1, "name": имя, "username": ник, "allowed": можно}
            for i, (имя, ник, можно) in enumerate(тг["люди"])
        ],
    })
    _чисто(w)
    w.evaluate_js("ui.audioSheet.hidden = false; showPrefsTab('telegram')")
    _снять(w, язык, "15-telegram")

    # 11. Выбор модели заметок.
    w.evaluate_js(f"showPrefsTab('notes'); renderLlmSettings({_js(llm)})")
    _снять(w, язык, "16-modeli")

    # 13. Ввод ключа лицензии. Пробный период идёт, полоса сверху видна.
    _подменить(w, "license_state", {"licensed": False, "trial": {"left": 7, "limit": 10, "over": False}})
    w.evaluate_js(
        "showPrefsTab('license'); state.licenseBarHidden = false; refreshLicense();"
        " ui.licenseKey.value = 'KSPK1.eyJ2IjoxLCJ0byI6Ik1hcmlh…';"
    )
    _снять(w, язык, "18-licenzija")

    # Диктовка: была в прошлых снимках, нужна и инструкции.
    w.evaluate_js("showPrefsTab('dictation')")
    _снять(w, язык, "19-diktovka")

    w.evaluate_js(f"applyTheme({_js(тема_была)})")
    итог["готово"] = True


def _снять_язык(язык: str) -> None:
    tmp = Path(tempfile.mkdtemp(prefix=f"konspekt-инструкция-{язык}-"))

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
    service.settings.check_updates = False
    service.settings.window.width = ШИРИНА
    service.settings.window.height = ВЫСОТА
    service.settings.always_on_top = False

    ид = {"планёрка": _наполнить(service, язык)}
    ид.update(_наполнить_инструкцию(service, язык, ид["планёрка"]))

    llm = service.llm_status()
    llm.update(backend="local", local_model="smart", model_ready=True, ram_gb=16, auto_summary=True)
    for м in llm.get("local_models") or []:
        м["downloaded"] = м.get("code") in ("fast", "smart")

    окно = MainWindow(service)
    w = окно.create()
    итог: dict[str, object] = {}

    def сцена() -> None:
        try:
            _сцена(w, service, язык, ид, llm, итог)
        except Exception as e:  # noqa: BLE001
            итог["ошибка"] = repr(e)
        finally:
            try:
                окно.quit()
            except Exception:
                pass

    threading.Thread(target=сцена, daemon=True).start()
    webview.start()
    service.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    if "ошибка" in итог:
        raise RuntimeError(f"{язык}: {итог['ошибка']}")


def main() -> int:
    if sys.platform != "win32":
        print("Снимки делаются под Windows: там живёт само окно.")
        return 1
    языки = [a for a in sys.argv[1:] if a in ЯЗЫКИ] or list(ЯЗЫКИ)
    for язык in языки:
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

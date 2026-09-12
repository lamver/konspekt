"""Проверка обновлений: сравнение версий и разбор ответа.

Сеть не трогаем: подменяем `httpx.get` и смотрим, что приложение делает
с ответом. Главное, что проверяем, — молчание. Ложное «вышла новая
версия» раздражает сильнее, чем пропущенное обновление, а битый или
чужой ответ не должен ронять старт приложения.
"""

from __future__ import annotations

import testenv  # noqa: F401  русский вывод в консоли Windows

import json
import sys
import time
import types

sys.path.insert(0, ".")

from app.core import version_check
from app.core.events import NEW_VERSION, bus
from app.core.settings import Settings


def check_versions() -> None:
    newer = version_check.is_newer

    assert newer("0.5.0", "0.4.9")
    assert newer("1.0.0", "0.99.99")
    assert newer("0.4.10", "0.4.9"), "числа сравниваются как числа, а не строки"
    assert newer("v0.5", "0.4.9"), "префикс v в теге допустим"
    assert newer("0.5", "0.4.9"), "короткая версия дополняется нулями"

    assert not newer("0.4.9", "0.4.9")
    assert not newer("0.4.8", "0.4.9")
    assert not newer("0.4", "0.4.0")

    # Мусор в ответе не должен звать обновляться.
    for junk in ("", "latest", "не версия", "0.x.1", None, 5):
        assert not newer(junk, "0.4.0"), f"мусор {junk!r} принят за версию"

    print("[ok] версии сравниваются правильно, мусор отвергается")


class FakeService:
    def __init__(self, **overrides) -> None:
        self.settings = Settings(**overrides)


def fake_response(payload, status=200):
    class Response:
        def raise_for_status(self):
            if status != 200:
                raise RuntimeError(f"HTTP {status}")

        def json(self):
            if isinstance(payload, Exception):
                raise payload
            return payload

    return Response()


def collect(monkey_payload, service, saved):
    """Прогнать проверку с подменённой сетью, вернуть пойманные события."""
    events: list[dict] = []
    off = bus.on(NEW_VERSION, events.append)

    real_get = version_check.httpx.get
    real_save = version_check.settings_mod.save
    version_check.httpx.get = lambda *a, **kw: fake_response(monkey_payload)
    version_check.settings_mod.save = lambda s: saved.append(s)
    try:
        checker = version_check.VersionChecker(service)
        checker._check()
    finally:
        version_check.httpx.get = real_get
        version_check.settings_mod.save = real_save
        off()
    return events


def check_notifies() -> None:
    saved: list = []
    service = FakeService()
    events = collect(
        {"version": "99.0.0", "url": "https://example.com/setup.exe", "notes": "тест"},
        service,
        saved,
    )
    assert len(events) == 1, f"ждали одно событие, получили {len(events)}"
    assert events[0]["latest"] == "99.0.0"
    assert events[0]["url"].endswith("setup.exe")
    assert service.settings.last_version_check > 0, "время проверки не записано"
    assert saved, "настройки не сохранены"
    print("[ok] свежая версия замечена и сообщена один раз")


def check_silent_when_current() -> None:
    service = FakeService()
    events = collect({"version": "0.0.1"}, service, [])
    assert not events, "старая версия на сервере не должна звать обновляться"
    print("[ok] на старую версию в ответе приложение молчит")


def check_disabled() -> None:
    service = FakeService(check_updates=False)
    events = collect({"version": "99.0.0"}, service, [])
    assert not events, "проверка выключена, а событие пришло"
    assert service.settings.last_version_check == 0, "выключенная проверка трогала настройки"
    print("[ok] выключенная проверка не ходит в сеть")


def check_daily() -> None:
    service = FakeService()
    service.settings.last_version_check = time.time() - 60  # минуту назад
    events = collect({"version": "99.0.0"}, service, [])
    assert not events, "проверка выполнена чаще раза в сутки"

    service.settings.last_version_check = time.time() - version_check.CHECK_INTERVAL - 1
    events = collect({"version": "99.0.0"}, service, [])
    assert events, "сутки прошли, а проверка не выполнилась"
    print("[ok] в сеть ходим не чаще раза в сутки")


def check_survives_junk() -> None:
    service = FakeService()
    for payload in (
        {},                              # пустой объект
        {"tag": "0.9"},                   # чужая схема
        {"version": None},
        ValueError("не json"),            # сервер отдал html
        [1, 2, 3],                        # список вместо объекта
    ):
        events = collect(payload, FakeService(), [])
        assert not events, f"на битом ответе {payload!r} позвали обновляться"
    print("[ok] битый ответ не роняет приложение и не зовёт обновляться")


def выпуск_опубликован(версия: str) -> bool | None:
    """Лежит ли на странице релизов сборка этой версии.

    Смотрим не на сам тег, а на приложенные файлы: релиз без установщика
    скачать нечего, и рассказывать о нём людям рано.

    Три ответа, а не два. `None` значит «не смогли узнать»: обрыв связи
    нельзя выдавать за «релиза нет», иначе проверка снова промолчит
    ровно там, где должна ругаться. Ради этого же несколько попыток —
    один таймаут на медленной сети не повод считать релиз ненайденным.
    """
    адрес = (
        "https://api.github.com/repos/lamver/konspekt-releases"
        f"/releases/tags/v{версия}"
    )
    беда = None
    for _ in range(3):
        try:
            ответ = version_check.httpx.get(
                адрес, timeout=20.0, follow_redirects=True
            )
        except Exception as e:
            беда = e
            continue
        if ответ.status_code == 404:
            return False
        if ответ.status_code != 200:
            беда = RuntimeError(f"HTTP {ответ.status_code}")
            continue
        return bool(ответ.json().get("assets"))
    print(f"[пропуск] не смогли спросить про релиз: {беда}")
    return None


def check_published_matches_build() -> None:
    """`latest.json` в сети должен знать про текущую версию.

    Единственная проверка здесь, которая ходит в сеть, и не зря: при
    выпуске 0.5.0 обновили релиз, а `latest.json` остался на 0.4.0.
    Внешне всё было исправно, но у людей кнопка «Проверить сейчас»
    отвечала «установлена свежая версия», сидя на старой сборке.
    Молча выйти отсюда нельзя: без сети пропускаем, но говорим об этом.
    """
    from app import __version__

    try:
        release = version_check.fetch_latest()
    except Exception as e:
        print(f"[пропуск] нет связи с сервером обновлений: {e}")
        return

    # Наша версия новее той, что в latest.json. До выпуска это нормально:
    # собрали, но ещё не опубликовали. А вот если релиз уже лежит
    # выложенным, значит про него просто забыли рассказать — и человек с
    # прошлой версией не узнает о новой ни при запуске, ни кнопкой
    # «Проверить сейчас». Ровно так 0.7.5 провисела месяц незамеченной,
    # пока проверка молча выходила отсюда.
    if version_check.is_newer(__version__, release.version):
        if выпуск_опубликован(__version__):
            raise AssertionError(
                f"релиз {__version__} выложен, а latest.json остался на "
                f"{release.version}: обновление до людей не дойдёт. "
                f"Лечится: python tools/обновить_latest.py \"новость\""
            )
        print(f"[пропуск] версия {__version__} ещё не опубликована "
              f"(latest.json на {release.version})")
        return

    assert not version_check.is_newer(release.version, __version__), (
        f"latest.json отдаёт {release.version}, а собрана {__version__}: "
        "у пользователей проверка обновлений будет молчать"
    )
    print(f"[ok] latest.json знает про {release.version}, собрана {__version__}")


def check_forwarded_to_ui() -> None:
    """Событие о новой версии должно доезжать до окна.

    Проверка находила обновление и писала строчку в журнал, а список
    транслируемых в интерфейс тем её не содержал: человек не видел
    ничего. Поэтому список проверяем явно.
    """
    from app.ui.window import FORWARDED_EVENTS

    assert NEW_VERSION in FORWARDED_EVENTS, (
        "событие о новой версии не транслируется в интерфейс"
    )
    print("[ok] новость об обновлении доезжает до окна")


def check_keeps_watching() -> None:
    """Программа, висящая в трее неделями, тоже узнаёт об обновлении.

    Проверка версии шла только при запуске. Konspekt закрывают крестиком
    в трей, а не выходом, поэтому такой человек не увидел бы ни одного
    обновления, сколько их ни выпускай. Ждём не сутки: промежуток
    пробуждения задаётся переменной окружения.
    """
    saved: list = []
    service = FakeService()
    events: list[dict] = []
    off = bus.on(NEW_VERSION, events.append)

    real_get = version_check.httpx.get
    real_save = version_check.settings_mod.save
    real_wake = version_check.WAKE_INTERVAL
    version_check.httpx.get = lambda *a, **kw: fake_response({"version": "99.0.0"})
    version_check.settings_mod.save = lambda s: saved.append(s)
    version_check.WAKE_INTERVAL = 0.05
    try:
        checker = version_check.VersionChecker(service)
        checker.watch()
        # Ждём максимум секунду: будильник должен сработать сам.
        deadline = time.time() + 1.0
        while not events and time.time() < deadline:
            time.sleep(0.02)
        assert events, "программа проработала дольше промежутка и не проверила версию"

        # И останавливается по команде, а не живёт вечным потоком.
        checker.stop()
        time.sleep(0.15)
        было = len(events)
        service.settings.last_version_check = 0  # разрешаем следующую проверку
        time.sleep(0.15)
        assert len(events) == было, "перепроверка продолжается после остановки"
    finally:
        version_check.httpx.get = real_get
        version_check.settings_mod.save = real_save
        version_check.WAKE_INTERVAL = real_wake
        off()
    print("[ok] долго живущая программа сама перепроверяет обновления")


def check_notes_language() -> None:
    """Новость об обновлении показывается на языке интерфейса.

    Обратная совместимость важнее удобства: до 0.8.0 `notes` был строкой,
    и установленные копии программы разбирают именно строку. Если
    положить в latest.json словарь раньше, чем они обновятся, человек
    увидит в окне `{'ru': ...}` целиком. Поэтому строку разбираем как
    прежде, а словарь понимаем заранее — чтобы он стал безопасен, когда
    старых копий не останется.
    """
    выбрать = version_check.выбрать_новость

    словарь = {"ru": "по-русски", "en": "in english", "es": "en espanol"}
    assert выбрать(словарь, "ru") == "по-русски"
    assert выбрать(словарь, "en") == "in english"
    assert выбрать(словарь, "es") == "en espanol"

    # Языка нет в словаре — берём английский, а не первый попавшийся.
    assert выбрать(словарь, "sr") == "in english", "запасной язык не английский"
    assert выбрать({"ru": "по-русски"}, "sr") == "по-русски", (
        "нет английского — должен подойти русский, а не пустота"
    )

    # Пустой перевод — это отсутствующий перевод, а не «без описания».
    assert выбрать({"sr": "   ", "en": "in english"}, "sr") == "in english"

    # Старый формат обязан работать дословно, иначе сломаем выпущенные копии.
    assert выбрать("просто строка", "en") == "просто строка"

    # Мусор вместо новости не должен доехать до окна.
    for junk in (None, 5, [1, 2], {"ru": 7}, {}):
        assert выбрать(junk, "ru") == "", f"мусор {junk!r} принят за новость"

    print("[ok] новость об обновлении выбирается по языку интерфейса")


def check_notes_language_reaches_user() -> None:
    """Язык из настроек доезжает до новости, а не теряется по дороге.

    Разбор по языку сам по себе бесполезен, если проверка обновлений
    зовёт его всегда с русским: человек с английским интерфейсом всё
    равно увидит русский текст.
    """
    многоязычная = {
        "version": "99.0.0",
        "url": "https://example.com/setup.exe",
        "notes": {"ru": "по-русски", "en": "in english"},
    }

    service = FakeService(language="en")
    events = collect(многоязычная, service, [])
    assert events, "обновление не найдено"
    assert events[0]["notes"] == "in english", (
        f"язык интерфейса не доехал до новости: {events[0]['notes']!r}"
    )

    service = FakeService(language="ru")
    events = collect(многоязычная, service, [])
    assert events[0]["notes"] == "по-русски"

    print("[ok] язык интерфейса доезжает до новости об обновлении")

def check_latest_format_guard() -> None:
    """Скрипт выпуска не даёт положить переводы раньше срока.

    Словарь в latest.json безопасен только тогда, когда его понимают все
    установленные копии. Копии до 0.8.1 ждут строку и покажут человеку
    словарь целиком, со скобками, — а увидим мы это уже у пользователей,
    потому что у себя запустим свежую сборку.

    Согласия спрашиваем всегда, независимо от выпускаемой версии. Это
    цена прошлой ошибки: раньше условие сравнивало выпускаемую версию с
    порогом, а она всегда новее порога, и предупреждение не сработало ни
    разу. Знать, какие копии стоят у людей, мы не можем, поэтому решает
    человек, а не сравнение версий.
    """
    sys.path.insert(0, "tools")
    import importlib

    обновить = importlib.import_module("обновить_latest")

    class Аргументы:
        def __init__(self, **поля):
            for язык in обновить.ЯЗЫКИ:
                setattr(self, язык, None)
            self.всё_равно = False
            self.новость = None
            for имя, значение in поля.items():
                setattr(self, имя, значение)

    # Один русский текст — старый формат, работает у всех.
    assert обновить.собрать_новость(Аргументы(ru="новость"), "0.8.0") == "новость"

    # Переводы без согласия — отказ, и в отказе назван настоящий порог.
    try:
        обновить.собрать_новость(Аргументы(ru="а", en="b"), "0.8.0")
    except SystemExit as e:
        assert обновить.ЗНАЕТ_ПРО_ПЕРЕВОДЫ in str(e), (
            "в отказе не сказано, с какой версии переводы безопасны"
        )
    else:
        raise AssertionError(
            "переводы приняты для 0.8.0: у людей в окне будет словарь со скобками"
        )

    # С явным согласием — можно: иногда старых копий уже не осталось.
    принято = обновить.собрать_новость(Аргументы(ru="а", en="b", всё_равно=True), "0.8.0")
    assert принято == {"ru": "а", "en": "b"}

    # Ровно та беда, что случилась с 0.10.0: выпускаем новую версию, а
    # словарь всё равно едет к старым копиям. Номер выпуска ничего не
    # решает, согласие обязательно.
    try:
        обновить.собрать_новость(Аргументы(ru="а", en="b"), "0.10.0")
    except SystemExit:
        pass
    else:
        raise AssertionError(
            "новая версия отменила вопрос: так словарь и уехал в 0.10.0"
        )

    # Пустая новость — не выпуск: человек увидел бы обновление без описания.
    try:
        обновить.собрать_новость(Аргументы(), "0.9.0")
    except SystemExit:
        pass
    else:
        raise AssertionError("выпуск без единой новости разрешён")

    print("[ok] переводы не попадут в latest.json раньше, чем их поймут")


def check_threshold_matches_tags() -> None:
    """Порог переводов сверяется с выпущенными тегами, а не с памятью.

    Порог уже был однажды неверен: стояло 0.9.0, хотя словарь понимают
    начиная с 0.8.1. Число в константе проверить глазами нельзя, зато
    можно спросить сам репозиторий: в каком теге появился разбор словаря
    в `version_check.py`. Ошиблись в константе — проверка падает.
    """
    import subprocess

    sys.path.insert(0, "tools")
    import importlib

    обновить = importlib.import_module("обновить_latest")

    теги = subprocess.run(["git", "tag", "-l", "v*"], capture_output=True,
                          text=True, encoding="utf-8").stdout.split()
    if not теги:
        print("[..] тегов нет, порог сверить не с чем")
        return

    def номер(тег: str) -> tuple[int, ...]:
        try:
            return tuple(int(ч) for ч in тег.lstrip("v").split("."))
        except ValueError:
            return (0,)

    понимают = []
    for тег in sorted(теги, key=номер):
        файл = subprocess.run(
            ["git", "show", f"{тег}:app/core/version_check.py"],
            capture_output=True, text=True, encoding="utf-8")
        if файл.returncode != 0:
            continue
        if "выбрать_новость" in файл.stdout or "isinstance(notes, dict)" in файл.stdout:
            понимают.append(тег)

    if not понимают:
        print("[..] ни один тег не разбирает словарь, сверять нечего")
        return

    первый = понимают[0].lstrip("v")
    assert первый == обновить.ЗНАЕТ_ПРО_ПЕРЕВОДЫ, (
        f"порог {обновить.ЗНАЕТ_ПРО_ПЕРЕВОДЫ} расходится с кодом тегов: "
        f"словарь понимают начиная с {первый}"
    )
    print(f"[ok] порог переводов {первый} подтверждён кодом выпущенных тегов")

def main() -> int:
    check_versions()
    check_notifies()
    check_silent_when_current()
    check_disabled()
    check_daily()
    check_survives_junk()
    check_notes_language()
    check_notes_language_reaches_user()
    check_latest_format_guard()
    check_threshold_matches_tags()
    check_keeps_watching()
    check_forwarded_to_ui()
    check_published_matches_build()
    print("\nВсе проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

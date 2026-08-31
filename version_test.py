"""Проверка обновлений: сравнение версий и разбор ответа.

Сеть не трогаем: подменяем `httpx.get` и смотрим, что приложение делает
с ответом. Главное, что проверяем, — молчание. Ложное «вышла новая
версия» раздражает сильнее, чем пропущенное обновление, а битый или
чужой ответ не должен ронять старт приложения.
"""

from __future__ import annotations

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


def main() -> int:
    check_versions()
    check_notifies()
    check_silent_when_current()
    check_disabled()
    check_daily()
    check_survives_junk()
    print("\nВсе проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

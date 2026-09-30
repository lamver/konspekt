# -*- coding: utf-8 -*-
"""Счёт в чате: «1+2», потом «-9» даёт -6, а не -10.

Жалоба со снимка экрана (30 сентября): «Умная» модель на «1+2» ответила
3, а на «-9» ответила -10. Проверено на ней напрямую: пять раз из пяти
-10, на «+9» 18, на «*4» 5. Маленькая модель не считает, а угадывает,
поэтому примеры без букв считает программа.
"""
from __future__ import annotations

import testenv  # noqa: F401  песочница вместо боевого профиля

import tempfile
import threading
from pathlib import Path

from app.core.events import CHAT_ERROR, CHAT_MESSAGE, bus
from app.core.models import Meeting
from app.core.service import AppService
from app.llm.calc import ответ
from app.storage.db import Store

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


# --- Счёт ---------------------------------------------------------------------
случаи = [
    ("1+2", "", "3"), ("-9", "3", "-6"), ("+9", "3", "12"), ("*4", "3", "12"),
    ("/4", "3", "0,75"), ("-9", "-6", "-15"), ("2,5*2", "", "5"), ("10:4", "", "2,5"),
    ("(1+2)*3=", "", "9"), ("2^10", "", "1024"), ("3 × 4", "", "12"), ("8 ÷ 2", "", "4"),
    ("1/3", "", "0,3333333333"), ("-9", "2,5", "-6,5"), ("0.1+0.2", "", "0,3"),
    ("123456789*1000", "", "123456789000"),
]
for вопрос, прошлый, надо in случаи:
    вышло = ответ(вопрос, прошлый)
    проверить(вышло == надо, f"{вопрос!r} после {прошлый!r} = {надо}, а не {вышло!r}")

проверить(ответ("5/0") == "На ноль делить нельзя.", "деление на ноль названо, а не роняет чат")
import time as _t
_н = _t.time()
проверить(ответ("9^9^9") is None and _t.time() - _н < 1, "9^9^9 не вешает программу")
for не_пример, прошлый in [("2024", ""), ("-9", ""), ("-9", "Лёвин, к пятнице."), ("?", ""),
                           ("", ""), ("()", ""), ("1++", ""), ("9^9^9", ""), ("10**400", ""),
                           ("кто 1+2", ""), ("__import__('os')", ""), ("1e400", ""),
                           ("0x10+1", ""), ("1_000+1", ""), ("1e3+1", "")]:
    проверить(ответ(не_пример, прошлый) is None, f"{не_пример!r} после {прошлый!r} уходит модели")

# --- Сквозь сервис: модель не зовётся, ответ в базе ----------------------------


class Модель:
    enabled = True
    backend = "local"
    tier = "smart"
    звали = 0

    def client(self):
        Модель.звали += 1
        raise AssertionError("модель не должна считать")

    def status(self):
        return {"model_ready": True}

    def shutdown(self):
        pass


tmp = Path(tempfile.mkdtemp()) / "t.db"
service = AppService(store=Store(str(tmp)))
service.llm = Модель()
try:
    mid = service.store.create_meeting(Meeting(title="Счёт")).id

    def спросить(вопрос: str) -> dict:
        итог: dict = {}
        done = threading.Event()

        def ловить(p):
            if p.get("done"):
                итог.update(p["message"])
                done.set()

        стопы = [bus.on(CHAT_MESSAGE, ловить),
                 bus.on(CHAT_ERROR, lambda p: (итог.update(p), done.set()))]
        try:
            assert service.ask(mid, вопрос).get("ok")
            done.wait(10)
        finally:
            for s in стопы:
                s()
        return итог

    проверить(спросить("1+2").get("text") == "3", "1+2 = 3")
    проверить(спросить("-9").get("text") == "-6", "потом -9 даёт -6, как на калькуляторе")
    проверить(спросить("*2").get("text") == "-12", "и дальше продолжает счёт")
    проверить(Модель.звали == 0, "модель ни разу не позвали")
    тексты = [m.text for m in service.store.list_chat_messages(mid)]
    проверить(тексты == ["1+2", "3", "-9", "-6", "*2", "-12"], f"переписка в базе: {тексты}")
    проверить(not service._llm_busy, "после счёта модель свободна для следующего вопроса")
finally:
    service.store.close()

print()
print("Всё в порядке" if not БЕДЫ else f"Бед: {len(БЕДЫ)}")
raise SystemExit(1 if БЕДЫ else 0)

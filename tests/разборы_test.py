# -*- coding: utf-8 -*-
"""Карточки разбора встречи: хранятся отдельно, стареют честно.

Что сторожит:
1. Разбор по одной методике не затирает другой и итоги встречи.
2. Встреча дополнилась после разбора — карточка помечена устаревшей.
3. Разборы без модели (разговор, тон) есть у любой встречи сразу.
4. Удалили встречу — ушли и её разборы: текст разбора пересказывает
   разговор, и держать его после удаления встречи нельзя.
5. В модель уходит форма методики, а не общий промпт итогов.
6. Встречу удалили посреди разбора — программа не падает.
"""
from __future__ import annotations

import testenv  # noqa: F401  песочница вместо боевого профиля

import tempfile
import threading
from pathlib import Path

from app.core.events import ANALYSIS_ERROR, ANALYSIS_READY, SUMMARY_READY, bus
from app.core.models import Meeting, Speaker, TranscriptSegment
from app.core.service import AppService
from app.llm import lenses
from app.storage.db import Store

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


class Клиент:
    def __init__(self, ответ: str) -> None:
        self.ответ = ответ
        self.запросы: list[list[dict]] = []

    def stream(self, messages, on_chunk=None, should_stop=None, **kw):
        self.запросы.append(messages)
        if on_chunk:
            on_chunk(self.ответ)
        return self.ответ

    def complete(self, messages, **kw):
        self.запросы.append(messages)
        return "выжимка части"


class Модель:
    enabled = True
    backend = "local"
    tier = "fast"

    def __init__(self) -> None:
        self.клиент = Клиент("")

    def client(self):
        return self.клиент

    def status(self):
        return {"model_ready": True}

    def shutdown(self):
        pass


def дождаться(событие_готово, запуск) -> dict:
    итог: dict = {}
    done = threading.Event()
    стопы = [bus.on(e, lambda p: (итог.update(p), done.set())) for e in событие_готово]
    try:
        r = запуск()
        assert r.get("ok"), r
        done.wait(10)
    finally:
        for s in стопы:
            s()
    return итог


def реплика(mid: str, i: int, track: Speaker, who: str, text: str) -> TranscriptSegment:
    return TranscriptSegment(meeting_id=mid, speaker=track, text=text,
                             start=i * 10.0, end=i * 10.0 + 8, voice_label=who)


tmp = Path(tempfile.mkdtemp()) / "t.db"
service = AppService(store=Store(str(tmp)))
модель = Модель()
service.llm = модель
try:
    mid = service.store.create_meeting(Meeting(title="Звонок клиенту")).id
    фразы = [
        (Speaker.ME, "Валерий", "Расскажите, как у вас сейчас устроен учёт встреч?"),
        (Speaker.THEM, "Анна", "Пишем руками в блокнот, половина договорённостей теряется."),
        (Speaker.ME, "Валерий", "А во что обходятся потерянные договорённости?"),
        (Speaker.THEM, "Анна", "Срываем сроки, бюджет на это у нас до ста тысяч в год."),
    ]
    for i, (t, w, x) in enumerate(фразы):
        service.store.add_segment(реплика(mid, i, t, w, x))

    # --- 3. Без модели карточки есть сразу -------------------------------
    карточки = {c["kind"]: c for c in service.list_analyses(mid)}
    проверить(list(карточки) == list(lenses.ПОРЯДОК), "все карточки на месте и по порядку")
    проверить(bool(карточки["talk"]["text"]) and "Анна" in карточки["talk"]["text"],
              "разбор разговора готов без модели и знает говорящих")
    проверить(not карточки["sales"]["text"], "разбор по методике не делается сам")
    проверить(not any(c["stale"] for c in карточки.values()), "у свежей встречи ничего не устарело")

    # --- 5. В модель уходит форма методики -------------------------------
    модель.клиент = Клиент("**Проблемы и боли**\n- теряются договорённости")
    итог = дождаться([ANALYSIS_READY, ANALYSIS_ERROR], lambda: service.run_analysis(mid, "sales"))
    проверить(итог.get("kind") == "sales" and "теряются" in итог.get("text", ""),
              "разбор по SPIN пришёл событием с нужным видом")
    запрос = модель.клиент.запросы[-1]
    проверить("SPIN" in запрос[0]["content"] and "**Бюджет**" in запрос[1]["content"],
              "модель получила форму SPIN/BANT, а не промпт итогов")
    проверить("Анна: Срываем сроки" in запрос[1]["content"], "модель получила расшифровку")

    # --- 1. Разборы не затирают друг друга -------------------------------
    модель.клиент = Клиент("**Опыт кандидата**\n- не обсуждалось")
    дождаться([ANALYSIS_READY, ANALYSIS_ERROR], lambda: service.run_analysis(mid, "interview"))
    модель.клиент = Клиент("**Решения**\n- ставим программу")
    дождаться([SUMMARY_READY], lambda: service.run_analysis(mid, "summary"))
    карточки = {c["kind"]: c for c in service.list_analyses(mid)}
    проверить("теряются" in карточки["sales"]["text"], "SPIN остался после STAR и итогов")
    проверить("Опыт кандидата" in карточки["interview"]["text"], "STAR сохранился")
    проверить("ставим программу" in карточки["summary"]["text"], "итоги стали карточкой")
    проверить(service.store.get_meeting(mid).summary.startswith("**Решения**"),
              "итоги по-прежнему лежат и в самой встрече")
    проверить(карточки["sales"]["model"] == "local:fast", "у разбора записано, какая модель его сделала")

    # --- 2. Встреча дополнилась ------------------------------------------
    разговор_до = карточки["talk"]["text"]
    service.store.add_segment(реплика(mid, 9, Speaker.THEM, "Анна", "И ещё: решение принимает директор."))
    карточки = {c["kind"]: c for c in service.list_analyses(mid)}
    проверить(карточки["sales"]["stale"], "после новой реплики SPIN помечен устаревшим")
    проверить(карточки["summary"]["stale"], "итоги тоже устарели")
    проверить(карточки["talk"]["text"] != разговор_до, "разбор разговора пересчитан сам")
    проверить(not карточки["talk"]["stale"], "разбор без модели не бывает устаревшим")

    # Старые встречи: саммари было, карточки нет — показываем без пометки.
    old = service.store.create_meeting(Meeting(title="Старая")).id
    service.store.update_meeting(old, summary="**Решения**\n- старое")
    к = {c["kind"]: c for c in service.list_analyses(old)}
    проверить("старое" in к["summary"]["text"] and not к["summary"]["stale"],
              "у старой встречи итоги видны в карточке и не пугают пометкой")

    # --- Ошибки -----------------------------------------------------------
    проверить(not service.run_analysis(mid, "нет-такого").get("ok"), "незнакомый разбор отклонён")
    пустая = service.store.create_meeting(Meeting(title="Пустая")).id
    итог = дождаться([ANALYSIS_READY, ANALYSIS_ERROR], lambda: service.run_analysis(пустая, "sales"))
    проверить(bool(итог.get("error")), "пустая встреча даёт понятную ошибку, а не пустую карточку")
    модель.enabled = False
    проверить(not service.run_analysis(mid, "sales").get("ok"), "при выключенной модели не запускается")
    модель.enabled = True

    # --- 6. Встречу удалили посреди разбора ------------------------------
    service.store.save_analysis("нет-встречи", "sales", "текст", "")
    проверить(True, "сохранение разбора удалённой встречи не роняет программу")

    # --- 4. Удаление уносит разборы --------------------------------------
    service.store.delete_meeting(mid)
    проверить(service.store.list_analyses(mid) == [], "удалили встречу — ушли и её разборы")
finally:
    service.store.close()

print()
print("Всё в порядке" if not БЕДЫ else f"Бед: {len(БЕДЫ)}")
raise SystemExit(1 if БЕДЫ else 0)

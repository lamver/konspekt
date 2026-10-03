# -*- coding: utf-8 -*-
"""Свой бот в Telegram: привязка, чужие чаты, голосовые, ссылки, итоги.

Настоящего Telegram здесь нет: Bot API подменён httpx.MockTransport, а
программа — простым заместителем. Проверяем то, что бьёт по человеку:
- бот не слушается чужих: без кода привязки и из чужого чата ничего не
  расшифровывает и не тратит пробные встречи;
- голосовое уходит в тот же импорт, что и перетащенный файл, в папку
  «Telegram», и ответ приходит в тот же чат;
- длинная расшифровка режется на сообщения не длиннее 4096 знаков;
- отозванный токен останавливает бота, а не долбит Telegram вечно.
"""
from __future__ import annotations

import testenv  # noqa: F401

import json
import tempfile
import threading
import time
from pathlib import Path

import httpx

from app.core import telegram_bot as tb
from app.core.events import IMPORT_PROGRESS, SUMMARY_READY, bus
from app.core.i18n import t as перевод

БЕДЫ: list[str] = []
ХОЗЯИН = 111
ЧУЖОЙ = 222


def проверить(ок: bool, что: str) -> None:
    print(("[ok] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


class Телеграм:
    """Поддельный Bot API: копит отправленное, отдаёт файлы и обновления."""

    def __init__(self) -> None:
        self.отправлено: list[tuple[int, str]] = []
        self.скачано: list[str] = []
        self.обновления: list[dict] = []
        self.ответ_на_опрос: int | None = None  # код ошибки для getUpdates

    def __call__(self, запрос: httpx.Request) -> httpx.Response:
        путь = запрос.url.path
        if "/file/bot" in путь:
            self.скачано.append(путь)
            return httpx.Response(200, content=b"OggS fake audio")
        метод = путь.rsplit("/", 1)[-1]
        тело = json.loads(запрос.content or b"{}")
        if метод == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "konspekt_test_bot"}})
        if метод == "sendMessage":
            self.отправлено.append((тело["chat_id"], тело["text"]))
            return httpx.Response(200, json={"ok": True, "result": {}})
        if метод == "getFile":
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/file_7.oga"}})
        if метод == "getUpdates":
            if self.ответ_на_опрос:
                return httpx.Response(self.ответ_на_опрос, json={
                    "ok": False, "error_code": self.ответ_на_опрос, "description": "нет"})
            пачка, self.обновления = self.обновления, []
            return httpx.Response(200, json={"ok": True, "result": пачка})
        return httpx.Response(404, json={"ok": False, "error_code": 404})

    def кому(self, chat_id: int) -> list[str]:
        return [текст for кто, текст in self.отправлено if кто == chat_id]


class Программа:
    """Заместитель сервиса: то, что бот зовёт у программы."""

    def __init__(self) -> None:
        self.импорт: list[tuple[list[str], str | None]] = []
        self.ссылки: list[str] = []
        self.итоги: list[str] = []
        self.проба_кончилась = False
        self.модель_есть = True
        self.расшифровка = "Анна: Сдвигаем запуск на четырнадцатое.\nИгорь: Смету пришлю до пятницы."

    def import_files(self, paths, folder_id=None):
        if self.проба_кончилась:
            return []
        self.импорт.append((paths, folder_id))
        return [{"id": f"task{len(self.импорт)}", "status": "waiting"}]

    def import_link(self, text, folder_id=None):
        if "http" not in text:
            return {"ok": False, "error": "not link"}
        self.ссылки.append(text)
        return {"ok": True, "task": {"id": "link1"}}

    def transcript_text(self, meeting_id):
        return self.расшифровка

    def generate_summary(self, meeting_id):
        self.итоги.append(meeting_id)
        return {"ok": True}

    def telegram_can_summarize(self):
        return self.модель_есть

    def telegram_folder_id(self):
        return "folder-tg"

    def _msg(self, key, **vars):
        return перевод(key, "ru", **vars)


def сообщение(chat_id: int, **поля) -> dict:
    return {"chat": {"id": chat_id, "type": "private", "first_name": "Валерий"}, **поля}


def собрать(хозяин: int = 0):
    тг = Телеграм()
    программа = Программа()
    привязки: list[tuple[int, str]] = []
    клиент = httpx.Client(transport=httpx.MockTransport(тг))
    бот = tb.ТелеграмБот(программа, "123:TEST", хозяин=хозяин,
                         при_привязке=lambda c, и: привязки.append((c, и)),
                         папка=Path(tempfile.mkdtemp(prefix="tg-test-")), клиент=клиент)
    return бот, тг, программа, привязки


# --- Привязка ------------------------------------------------------------
бот, тг, программа, привязки = собрать()
бот.обработать(сообщение(ХОЗЯИН, text="привет"))
проверить(тг.кому(ХОЗЯИН) == [перевод("python.telegram.need_code", "ru")],
          "без кода бот просит код из программы")
бот.обработать(сообщение(ХОЗЯИН, voice={"file_id": "v", "file_unique_id": "u", "file_size": 1000}))
проверить(not программа.импорт, "до привязки голосовое не расшифровывается")
бот.обработать(сообщение(ХОЗЯИН, text="000000"))
проверить(not привязки, "неверный код не привязывает")
бот.обработать(сообщение(ХОЗЯИН, text=f"/start {бот.код}"))
проверить(привязки == [(ХОЗЯИН, "Валерий")] and бот.хозяин == ХОЗЯИН, "верный код привязывает этот чат")
проверить(тг.кому(ХОЗЯИН)[-1] == перевод("python.telegram.paired", "ru"), "после привязки бот объясняет, что умеет")
проверить(бот.состояние()["код"] == "", "после привязки код больше не показывается")

# --- Чужие ----------------------------------------------------------------
бот.обработать(сообщение(ЧУЖОЙ, voice={"file_id": "v", "file_unique_id": "u", "file_size": 1000}))
проверить(тг.кому(ЧУЖОЙ) == [перевод("python.telegram.foreign", "ru")] and not программа.импорт,
          "чужой чат получает отказ, его голосовое не расшифровывается")
бот.обработать(сообщение(ЧУЖОЙ, text=f"{бот.код}"))
проверить(бот.хозяин == ХОЗЯИН, "чужой не перехватывает бота даже с кодом")
группа = {"chat": {"id": -5, "type": "group"}, "voice": {"file_id": "v", "file_unique_id": "u"}}
было = len(тг.отправлено)
бот.обработать(группа)
проверить(len(тг.отправлено) == было and not программа.импорт, "в группах бот молчит")

# --- Голосовое: импорт, расшифровка, итоги ---------------------------------
бот, тг, программа, _ = собрать(хозяин=ХОЗЯИН)
бот.запустить = lambda: None  # опрос не нужен: сообщения подаём сами
отписки = [bus.on(IMPORT_PROGRESS, бот._на_импорт), bus.on(SUMMARY_READY, бот._на_итоги)]
try:
    бот.обработать(сообщение(ХОЗЯИН, voice={"file_id": "v1", "file_unique_id": "uq1", "file_size": 50_000}))
    файлы, папка = программа.импорт[0]
    проверить(папка == "folder-tg", "голосовое ложится в папку «Telegram»")
    проверить(Path(файлы[0]).exists() and Path(файлы[0]).suffix == ".ogg",
              f"голосовое скачано в папку программы: {Path(файлы[0]).name}")
    проверить(тг.кому(ХОЗЯИН)[-1] == перевод("python.telegram.accepted", "ru"), "бот отвечает «Принял»")

    bus.emit(IMPORT_PROGRESS, {"task": {"id": "task1", "status": "running", "meeting_id": "m1"}})
    проверить(len(тг.кому(ХОЗЯИН)) == 1, "пока разбор идёт, бот молчит")
    bus.emit(IMPORT_PROGRESS, {"task": {"id": "task1", "status": "done", "meeting_id": "m1"}})
    последнее = тг.кому(ХОЗЯИН)[-1]
    проверить(последнее.startswith("Расшифровка:") and "Смету пришлю до пятницы" in последнее,
              "готовая расшифровка приходит в чат")
    проверить(программа.итоги == ["m1"], "модель есть — итоги запускаются сами")
    bus.emit(SUMMARY_READY, {"meeting_id": "m1", "summary": "## Решения\n- **Запуск** 14 ноября\n* смета до пятницы"})
    итоги = тг.кому(ХОЗЯИН)[-1]
    проверить(итоги.startswith("Итоги:") and "**" not in итоги and "#" not in итоги and "• Запуск 14 ноября" in итоги,
              "итоги приходят простым текстом, без звёздочек и решёток")
    bus.emit(SUMMARY_READY, {"meeting_id": "m1", "summary": "ещё раз"})
    проверить(тг.кому(ХОЗЯИН)[-1] == итоги, "итоги одной встречи не дублируются")

    # Нет модели: расшифровка и подсказка, без молчаливой загрузки 1,8 ГБ.
    программа.модель_есть = False
    бот.обработать(сообщение(ХОЗЯИН, audio={"file_id": "a2", "file_unique_id": "uq2", "file_size": 9000, "file_name": "звонок.mp3"}))
    bus.emit(IMPORT_PROGRESS, {"task": {"id": "task2", "status": "done", "meeting_id": "m2"}})
    проверить(тг.кому(ХОЗЯИН)[-1] == перевод("python.telegram.no_model", "ru") and программа.итоги == ["m1"],
              "без модели — расшифровка и подсказка, итоги не запускаются")
    проверить(Path(программа.импорт[1][0][0]).suffix == ".oga" and "звонок" in Path(программа.импорт[1][0][0]).name,
              "аудио сохраняется с именем файла и расширением с сервера")

    # Длинная расшифровка режется на сообщения.
    программа.расшифровка = "\n".join(f"Анна: длинная реплика номер {i} про запуск и смету" for i in range(400))
    бот.обработать(сообщение(ХОЗЯИН, video_note={"file_id": "c3", "file_unique_id": "uq3", "file_size": 9000}))
    было = len(тг.кому(ХОЗЯИН))
    bus.emit(IMPORT_PROGRESS, {"task": {"id": "task3", "status": "done", "meeting_id": "m3"}})
    куски = тг.кому(ХОЗЯИН)[было:]
    проверить(len(куски) > 3 and all(len(к) <= 4096 for к in куски),
              f"длинная расшифровка разбита на {len(куски)} сообщений не длиннее 4096")

    # Ошибка разбора.
    бот.обработать(сообщение(ХОЗЯИН, voice={"file_id": "v4", "file_unique_id": "uq4", "file_size": 100}))
    bus.emit(IMPORT_PROGRESS, {"task": {"id": "task4", "status": "failed", "meeting_id": None}})
    проверить(тг.кому(ХОЗЯИН)[-1] == перевод("python.telegram.failed", "ru"), "не расшифровалось — бот говорит об этом")
finally:
    for о in отписки:
        о()

# --- Доступ для других ------------------------------------------------------
# Решает программа: бот спрашивает доступ() и сообщает, кто написал. Здесь
# настоящие методы сервиса на голых настройках, без окна и базы.
from app.core import service as service_mod  # noqa: E402
from app.core import settings as settings_mod  # noqa: E402

сохранено: list[int] = []
настоящее_сохранение = settings_mod.save
settings_mod.save = lambda s: сохранено.append(1)
try:
    сервис = service_mod.AppService.__new__(service_mod.AppService)
    сервис.settings = settings_mod.Settings()
    сервис.settings.telegram.chat_id = ХОЗЯИН
    сервис._бот = None

    бот, тг, программа, _ = собрать(хозяин=ХОЗЯИН)
    бот.доступ = сервис._telegram_allowed
    бот.при_сообщении = сервис._telegram_seen
    бот.обработать(сообщение(ЧУЖОЙ, voice={"file_id": "v", "file_unique_id": "s1", "file_size": 100}))
    люди = сервис.telegram_state()["users"]
    проверить(люди == [{"id": ЧУЖОЙ, "name": "Валерий", "username": "", "allowed": False}],
              "написавший незнакомец попадает в список без доступа")
    проверить(тг.кому(ЧУЖОЙ) == [перевод("python.telegram.foreign", "ru")] and not программа.импорт,
              "без галочки — отказ, запись не расшифровывается")
    бот.обработать(сообщение(ЧУЖОЙ, text="ещё раз"))
    проверить(len(сервис.telegram_state()["users"]) == 1, "повторное сообщение не плодит строки в списке")

    сервис.telegram_set_user(ЧУЖОЙ, True)
    бот.обработать(сообщение(ЧУЖОЙ, voice={"file_id": "v", "file_unique_id": "s2", "file_size": 100}))
    проверить(len(программа.импорт) == 1 and тг.кому(ЧУЖОЙ)[-1] == перевод("python.telegram.accepted", "ru"),
              "после галочки голосовое отмеченного человека расшифровывается")
    название = Path(программа.импорт[0][0][0]).stem
    проверить(название.startswith("Валерий · голосовое "),
              f"встреча называется по отправителю: «{название}»")
    with бот._замок:
        чат = (бот._ждём_расшифровку.get("task1") or (None,))[0]
    проверить(чат == ЧУЖОЙ, "расшифровка уйдёт тому, кто прислал, а не владельцу")

    сервис.telegram_set_user(ЧУЖОЙ, False)
    бот.обработать(сообщение(ЧУЖОЙ, voice={"file_id": "v", "file_unique_id": "s3", "file_size": 100}))
    проверить(len(программа.импорт) == 1, "сняли галочку — доступ закрыт снова")

    сервис.telegram_set_access("all")
    бот.обработать(сообщение(333, voice={"file_id": "v", "file_unique_id": "s4", "file_size": 100}))
    проверить(len(программа.импорт) == 2 and any(u["id"] == 333 for u in сервис.telegram_state()["users"]),
              "режим «всем»: незнакомец обслуживается и виден в списке")
    сервис.telegram_set_access("chosen")
    проверить(not сервис._telegram_allowed(333) and сервис._telegram_allowed(ЧУЖОЙ) is False,
              "вернули «только отмеченные» — незнакомцы снова без доступа")
finally:
    settings_mod.save = настоящее_сохранение

# --- Ограничения ----------------------------------------------------------
бот, тг, программа, _ = собрать(хозяин=ХОЗЯИН)
бот.обработать(сообщение(ХОЗЯИН, video={"file_id": "big", "file_unique_id": "b", "file_size": 25 * 1024 * 1024}))
проверить(тг.кому(ХОЗЯИН) == [перевод("python.telegram.too_big", "ru")] and not тг.скачано,
          "файл больше 20 МБ не качается, бот объясняет почему")
программа.проба_кончилась = True
бот.обработать(сообщение(ХОЗЯИН, voice={"file_id": "v", "file_unique_id": "t", "file_size": 100}))
проверить(тг.кому(ХОЗЯИН)[-1] == перевод("python.telegram.trial_over", "ru"),
          "после пробных встреч бот говорит про лицензию")
программа.проба_кончилась = False
бот.обработать(сообщение(ХОЗЯИН, text="посмотри https://rutube.ru/video/abc/"))
проверить(программа.ссылки and тг.кому(ХОЗЯИН)[-1] == перевод("python.telegram.accepted_link", "ru"),
          "ссылка на запись уходит в ту же загрузку по ссылке")
бот.обработать(сообщение(ХОЗЯИН, text="просто текст"))
проверить(тг.кому(ХОЗЯИН)[-1] == перевод("python.telegram.help", "ru"), "на обычный текст — подсказка")
бот.обработать(сообщение(ХОЗЯИН, document={"file_id": "d", "file_unique_id": "d", "mime_type": "application/pdf"}))
проверить(тг.кому(ХОЗЯИН)[-1] == перевод("python.telegram.help", "ru"), "PDF не принимается за запись")

# --- Опрос: отозванный токен ------------------------------------------------
бот, тг, программа, _ = собрать(хозяин=ХОЗЯИН)
тг.ответ_на_опрос = 401
поток = threading.Thread(target=бот._цикл, daemon=True)
поток.start()
поток.join(timeout=5)
проверить(not поток.is_alive() and бот.состояние()["ошибка"] == "bad_token",
          "отозванный токен останавливает опрос с понятной ошибкой")

бот, тг, программа, _ = собрать(хозяин=ХОЗЯИН)
тг.обновления = [{"update_id": 7, "message": сообщение(ХОЗЯИН, text="/help")}]
поток = threading.Thread(target=бот._цикл, daemon=True)
поток.start()
for _ in range(50):
    if тг.кому(ХОЗЯИН):
        break
    time.sleep(0.05)
бот._стоп.set()
тг.ответ_на_опрос = 409
поток.join(timeout=8)
проверить(тг.кому(ХОЗЯИН) == [перевод("python.telegram.help", "ru")] and бот.имя == "@konspekt_test_bot",
          "опрос забирает сообщения и отвечает; имя бота узнаётся")
проверить(not поток.is_alive(), "остановка опроса не висит")

# --- Записи из папки-источника -------------------------------------------
# Человек их боту не присылал: ему нужна суть с подписью, откуда запись, а
# не получасовая расшифровка звонка. Модель одна — пачка ждёт по очереди.
from app.core.events import SUMMARY_ERROR  # noqa: E402

бот, тг, программа, _ = собрать(хозяин=ХОЗЯИН)
занята: list[str] = []


def итоги_с_очередью(встреча):
    if занята:
        return {"ok": False, "error": "занята"}
    программа.итоги.append(встреча)
    занята.append(встреча)
    return {"ok": True}


программа.generate_summary = итоги_с_очередью
отписки = [bus.on(IMPORT_PROGRESS, бот._на_импорт), bus.on(SUMMARY_READY, бот._на_итоги),
           bus.on(SUMMARY_ERROR, бот._на_ошибку_итогов)]
try:
    бот.следить("p1", ХОЗЯИН, "📁 Звонки · Звонок +7 916 123-45-67")
    бот.следить("p2", ХОЗЯИН, "📁 Звонки · Звонок +7 999 000-00-00")
    bus.emit(IMPORT_PROGRESS, {"task": {"id": "p1", "status": "done", "meeting_id": "pm1"}})
    bus.emit(IMPORT_PROGRESS, {"task": {"id": "p2", "status": "done", "meeting_id": "pm2"}})
    проверить(not тг.кому(ХОЗЯИН), "запись из папки: расшифровку целиком не шлём, ждём итоги")
    проверить(программа.итоги == ["pm1"], "вторая встреча ждёт, пока модель занята первой")
    занята.clear()
    bus.emit(SUMMARY_READY, {"meeting_id": "pm1", "summary": "- **Клиент** просит скидку"})
    первое = тг.кому(ХОЗЯИН)[-1]
    проверить(первое.startswith("📁 Звонки · Звонок +7 916 123-45-67\n\nИтоги:") and "Клиент просит скидку" in первое,
              "итоги приходят с подписью, откуда запись")
    проверить(программа.итоги == ["pm1", "pm2"], "после первых итогов модель берётся за следующую встречу")
    занята.clear()
    bus.emit(SUMMARY_ERROR, {"meeting_id": "pm2", "error": "x"})
    проверить(тг.кому(ХОЗЯИН)[-1].startswith("📁 Звонки · Звонок +7 999") and
              перевод("python.telegram.summary_later", "ru") in тг.кому(ХОЗЯИН)[-1],
              "итоги не вышли — бот так и говорит, с подписью записи")

    программа.модель_есть = False
    программа.расшифровка = "Клиент: нужна скидка."
    бот.следить("p3", ХОЗЯИН, "📁 Звонки · Звонок")
    bus.emit(IMPORT_PROGRESS, {"task": {"id": "p3", "status": "done", "meeting_id": "pm3"}})
    проверить(тг.кому(ХОЗЯИН)[-1] == "📁 Звонки · Звонок\n\nРасшифровка:\n\nКлиент: нужна скидка.",
              "без модели вместо итогов приходит расшифровка с подписью")
    бот.следить("p4", ХОЗЯИН, "📁 Звонки · Звонок")
    bus.emit(IMPORT_PROGRESS, {"task": {"id": "p4", "status": "failed", "meeting_id": None}})
    проверить(тг.кому(ХОЗЯИН)[-1].startswith("📁 Звонки · Звонок\n\n"),
              "не расшифровалось — сообщение тоже с подписью записи")
finally:
    for о in отписки:
        о()

# --- Токен не попадает в журнал -------------------------------------------
# httpx пишет адрес каждого запроса, а у Telegram в адресе токен. Журнал
# прикладывают к жалобам: ключ от бота уезжал бы вместе с ним.
import io  # noqa: E402
import logging  # noqa: E402

поток_журнала = io.StringIO()
обработчик = logging.StreamHandler(поток_журнала)
журнал_httpx = logging.getLogger("httpx")
прежний = журнал_httpx.level
журнал_httpx.setLevel(logging.INFO)
журнал_httpx.addHandler(обработчик)
try:
    журнал_httpx.info('HTTP Request: %s %s "%s %d %s"', "POST",
                      "https://api.telegram.org/bot123456:AAHsecret_TOKEN-xyz/getUpdates", "HTTP/1.1", 200, "OK")
    журнал_httpx.info("HTTP Request: GET https://api.telegram.org/file/bot123456:AAHsecret_TOKEN-xyz/voice/1.oga")
finally:
    журнал_httpx.removeHandler(обработчик)
    журнал_httpx.setLevel(прежний)
записано = поток_журнала.getvalue()
проверить("AAHsecret" not in записано and записано.count("<токен скрыт>") == 2,
          "токен бота вычищен из журнала, и в запросах, и в скачивании файлов")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Бот Telegram работает как надо.")

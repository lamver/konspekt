"""Свой бот в Telegram: перешли голосовое — получи расшифровку и итоги.

Бот живёт в программе, а не на нашем сервере. Человек сам создаёт бота
у @BotFather и вставляет токен в настройки; программа спрашивает Telegram
о новых сообщениях (long polling), скачивает голосовое, расшифровывает его
у себя тем же импортом, что и перетащенный файл, и отвечает текстом.

Что куда уходит, честно:
- звук уже лежит в Telegram: человек сам его туда переслал;
- расшифровка и итоги делаются на этом компьютере;
- текст расшифровки и итоги бот отправляет в тот же чат, через серверы
  Telegram, как любое сообщение. Это и есть смысл бота.
Нашего сервера в этой цепочке нет.

Бот слушается хозяина и тех, кому хозяин открыл доступ. Кто нашёл бота по
имени, не заставит чужой компьютер расшифровывать его записи и не потратит
чужую лицензию: хозяин привязывается одноразовым кодом из окна программы,
остальные без доступа получают вежливый отказ и попадают в список во
вкладке «Telegram», где хозяин отмечает их галочкой. Можно открыть бота и
всем — это решение хозяина, и вкладка честно предупреждает, чем оно грозит.

Работает, только пока программа запущена: спросить Telegram о сообщениях
больше некому. Сообщения, присланные, пока компьютер выключен, Telegram
хранит сутки и отдаст при следующем запуске.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx

from . import секрет
from .events import IMPORT_PROGRESS, SUMMARY_ERROR, SUMMARY_READY, bus

log = logging.getLogger(__name__)


# Токен бота стоит прямо в адресе запроса, а httpx пишет адреса в журнал.
# Общий фильтр вычищает его (core/секрет.py).
секрет.прикрыть_журнал_запросов()

API = "https://api.telegram.org"
# Bot API отдаёт боту файлы не больше 20 МБ. Голосовые и кружки влезают
# всегда, длинная запись совещания — нет; про неё бот скажет прямо.
ЛИМИТ_ФАЙЛА = 20 * 1024 * 1024
# Сообщение в Telegram — до 4096 знаков; режем с запасом по строкам.
ДЛИНА_СООБЩЕНИЯ = 3900
ОПРОС_СЕКУНД = 25


class Сервис(Protocol):
    """Что боту нужно от программы. Отдельно, чтобы проверять без программы."""

    def import_files(self, paths: list[str], folder_id: str | None = None) -> list[dict[str, Any]]: ...
    def import_link(self, text: str, folder_id: str | None = None) -> dict[str, Any]: ...
    def transcript_text(self, meeting_id: str) -> str: ...
    def generate_summary(self, meeting_id: str) -> dict[str, Any]: ...
    def telegram_can_summarize(self) -> bool: ...
    def telegram_folder_id(self) -> str | None: ...
    def _msg(self, key: str, **vars: object) -> str: ...


class ОшибкаТелеграма(Exception):
    def __init__(self, код: int, описание: str = "") -> None:
        super().__init__(f"{код}: {описание}")
        self.код = код
        self.описание = описание


def нарезать(текст: str, предел: int = ДЛИНА_СООБЩЕНИЯ) -> list[str]:
    """Разбить длинный текст на сообщения по строкам, а где строка длинная — по словам."""
    текст = (текст or "").strip()
    куски: list[str] = []
    while len(текст) > предел:
        срез = текст[:предел]
        граница = срез.rfind("\n")
        if граница < предел // 2:
            граница = срез.rfind(" ")
        if граница < предел // 2:
            граница = предел
        куски.append(текст[:граница].rstrip())
        текст = текст[граница:].lstrip()
    if текст:
        куски.append(текст)
    return куски


def без_разметки(текст: str) -> str:
    """Markdown итогов в простой текст: звёздочки Telegram показал бы как есть."""
    строки = []
    for строка in (текст or "").splitlines():
        строка = re.sub(r"^\s{0,3}#{1,6}\s*", "", строка)
        строка = re.sub(r"\*\*(.+?)\*\*", r"\1", строка)
        строка = re.sub(r"__(.+?)__", r"\1", строка)
        строка = re.sub(r"`([^`]+)`", r"\1", строка)
        строка = re.sub(r"^(\s*)[*-]\s+", r"\1• ", строка)
        строки.append(строка)
    return "\n".join(строки).strip()


def _файл_из_сообщения(сообщение: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    """Звук или видео в сообщении: (описание файла, расширение)."""
    for поле, расширение in (("voice", ".ogg"), ("audio", ""), ("video_note", ".mp4"), ("video", ".mp4")):
        if сообщение.get(поле):
            return сообщение[поле], расширение
    документ = сообщение.get("document")
    if документ and str(документ.get("mime_type", "")).split("/")[0] in ("audio", "video"):
        return документ, ""
    return None


class ТелеграмБот:
    def __init__(
        self,
        сервис: Сервис,
        токен: str,
        хозяин: int = 0,
        при_привязке: Callable[[int, str], None] | None = None,
        папка: Path | None = None,
        клиент: httpx.Client | None = None,
        доступ: Callable[[int], bool] | None = None,
        при_сообщении: Callable[[int, str, str], None] | None = None,
    ) -> None:
        self.сервис = сервис
        self.токен = токен
        self.хозяин = int(хозяин or 0)
        self.при_привязке = при_привязке or (lambda chat_id, имя: None)
        # Кому кроме хозяина можно. Решает программа: список и режим живут
        # в настройках, а бот только спрашивает.
        self.доступ = доступ or (lambda chat_id: False)
        # Кто написал (кроме хозяина): чтобы человек появился в списке.
        self.при_сообщении = при_сообщении or (lambda chat_id, имя, ник: None)
        self.папка = папка or Path(".")
        self._клиент = клиент
        self._стоп = threading.Event()
        self._поток: threading.Thread | None = None
        self._замок = threading.Lock()
        # Новый код при каждом запуске: увиденный когда-то на чужом экране
        # код не годится навсегда.
        self.код = f"{secrets.randbelow(900000) + 100000}"
        self.имя = ""
        self.ошибка = ""
        # id задачи → (чат, заголовок). Заголовок есть у записей из
        # папки-источника: «📁 Звонки · Звонок +7 …», чтобы было видно, что
        # это и откуда, — человек их боту не присылал.
        self._ждём_расшифровку: dict[str, tuple[int, str | None]] = {}
        self._ждём_итоги: dict[str, tuple[int, str | None]] = {}   # id встречи → (чат, заголовок)
        # Модель одна: пачка звонков из АТС ждёт итогов по очереди, а не
        # теряется с «модель занята».
        self._очередь_итогов: list[tuple[str, int, str | None]] = []
        self._отписки: list[Callable[[], None]] = []

    # --- снаружи ----------------------------------------------------------

    def состояние(self) -> dict[str, Any]:
        return {
            "работает": bool(self._поток and self._поток.is_alive()),
            "имя": self.имя,
            "ошибка": self.ошибка,
            "привязан": bool(self.хозяин),
            "код": "" if self.хозяин else self.код,
        }

    def запустить(self) -> None:
        if self._поток and self._поток.is_alive():
            return
        self._стоп.clear()
        self._отписки = [
            bus.on(IMPORT_PROGRESS, self._на_импорт),
            bus.on(SUMMARY_READY, self._на_итоги),
            bus.on(SUMMARY_ERROR, self._на_ошибку_итогов),
        ]
        self._поток = threading.Thread(target=self._цикл, name="telegram-bot", daemon=True)
        self._поток.start()

    def остановить(self) -> None:
        self._стоп.set()
        for отписка in self._отписки:
            отписка()
        self._отписки = []
        if self._поток:
            self._поток.join(timeout=3)
        if self._клиент is not None:
            try:
                self._клиент.close()
            except Exception:
                pass
            self._клиент = None

    # --- Bot API ----------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._клиент is None:
            self._клиент = httpx.Client(timeout=httpx.Timeout(ОПРОС_СЕКУНД + 10, connect=15))
        return self._клиент

    def _вызов(self, метод: str, **параметры: Any) -> Any:
        ответ = self._http().post(f"{API}/bot{self.токен}/{метод}", json=параметры)
        try:
            данные = ответ.json()
        except ValueError:
            raise ОшибкаТелеграма(ответ.status_code, "не JSON") from None
        if not данные.get("ok"):
            raise ОшибкаТелеграма(int(данные.get("error_code") or ответ.status_code),
                                  str(данные.get("description") or ""))
        return данные.get("result")

    def проверить_токен(self) -> str:
        """Имя бота по токену. Неверный токен — ОшибкаТелеграма."""
        о_себе = self._вызов("getMe")
        self.имя = "@" + str(о_себе.get("username") or "")
        return self.имя

    def отправить(self, chat_id: int, текст: str) -> None:
        for кусок in нарезать(текст):
            try:
                self._вызов("sendMessage", chat_id=chat_id, text=кусок,
                            disable_web_page_preview=True)
            except (ОшибкаТелеграма, httpx.HTTPError):
                log.warning("Телеграм: сообщение не ушло", exc_info=True)
                return

    def _скачать(self, описание: dict[str, Any], расширение: str, отправитель: str = "") -> Path:
        файл = self._вызов("getFile", file_id=описание["file_id"])
        путь_на_сервере = файл["file_path"]
        if not расширение:
            расширение = Path(путь_на_сервере).suffix or ".bin"
        # Имя файла станет названием встречи: «Анна · голосовое 03.10 11.30»
        # находится в списке глазами, а «telegram-20261003-113012» — нет.
        # Свой каталог на каждый файл, чтобы имена не сталкивались.
        if описание.get("file_name"):
            имя = Path(описание["file_name"]).stem
        else:
            когда = time.strftime("%d.%m %H.%M")
            имя = f"{отправитель} · {self.сервис._msg('python.telegram.voice_title')} {когда}" if отправитель \
                else f"Telegram {когда}"
        имя = re.sub(r'[\\/:*?"<>|]+', "_", имя).strip()[:80] or "telegram"
        каталог = self.папка / str(описание["file_unique_id"])
        каталог.mkdir(parents=True, exist_ok=True)
        путь = каталог / f"{имя}{расширение}"
        with self._http().stream("GET", f"{API}/file/bot{self.токен}/{путь_на_сервере}") as поток:
            поток.raise_for_status()
            with путь.open("wb") as вывод:
                for кусок in поток.iter_bytes():
                    вывод.write(кусок)
        return путь

    # --- опрос --------------------------------------------------------------

    def _цикл(self) -> None:
        смещение = 0
        пауза = 5.0
        while not self._стоп.is_set():
            try:
                if not self.имя:
                    self.проверить_токен()
                обновления = self._вызов("getUpdates", offset=смещение,
                                         timeout=ОПРОС_СЕКУНД, allowed_updates=["message"])
                self.ошибка = ""
                пауза = 5.0
                for обновление in обновления or []:
                    смещение = max(смещение, int(обновление["update_id"]) + 1)
                    сообщение = обновление.get("message")
                    if сообщение:
                        try:
                            self.обработать(сообщение)
                        except Exception:
                            log.exception("Телеграм: сообщение не разобрано")
            except ОшибкаТелеграма as беда:
                if беда.код == 401:
                    # Токен отозвали у @BotFather: спрашивать дальше бессмысленно.
                    self.ошибка = "bad_token"
                    log.warning("Телеграм: токен не принят, бот остановлен")
                    return
                # 409: этим же ботом уже опрашивает другая копия программы.
                self.ошибка = "conflict" if беда.код == 409 else "api"
                log.info("Телеграм: %s, ждём %.0f с", беда, пауза)
                self._стоп.wait(пауза)
                пауза = min(пауза * 2, 120)
            except httpx.HTTPError as беда:
                self.ошибка = "network"
                log.info("Телеграм недоступен (%s), ждём %.0f с", беда.__class__.__name__, пауза)
                self._стоп.wait(пауза)
                пауза = min(пауза * 2, 120)

    # --- сообщения ----------------------------------------------------------

    def обработать(self, сообщение: dict[str, Any]) -> None:
        чат = сообщение.get("chat") or {}
        if чат.get("type") != "private":
            return  # в группы бот не ходит: там чужие люди и чужие голоса
        chat_id = int(чат["id"])
        текст = (сообщение.get("text") or "").strip()
        м = self.сервис._msg
        имя = " ".join(x for x in (чат.get("first_name"), чат.get("last_name")) if x) or чат.get("username") or ""
        ник = str(чат.get("username") or "")

        if not self.хозяин:
            if self.код in re.findall(r"\d{6}", текст):
                with self._замок:
                    self.хозяин = chat_id
                self.при_привязке(chat_id, имя)
                self.отправить(chat_id, м("python.telegram.paired"))
            else:
                self.отправить(chat_id, м("python.telegram.need_code"))
            return

        if chat_id != self.хозяин:
            self.при_сообщении(chat_id, имя, ник)
            if not self.доступ(chat_id):
                self.отправить(chat_id, м("python.telegram.foreign"))
                return

        файл = _файл_из_сообщения(сообщение)
        if файл:
            self._принять_файл(chat_id, *файл, отправитель=имя)
            return
        if текст and not текст.startswith("/"):
            self._принять_ссылку(chat_id, текст)
            return
        self.отправить(chat_id, м("python.telegram.help"))

    def _принять_файл(self, chat_id: int, описание: dict[str, Any], расширение: str,
                      отправитель: str = "") -> None:
        м = self.сервис._msg
        if int(описание.get("file_size") or 0) > ЛИМИТ_ФАЙЛА:
            self.отправить(chat_id, м("python.telegram.too_big"))
            return
        try:
            путь = self._скачать(описание, расширение, отправитель)
        except (ОшибкаТелеграма, httpx.HTTPError, OSError):
            log.warning("Телеграм: файл не скачался", exc_info=True)
            self.отправить(chat_id, м("python.telegram.download_failed"))
            return
        задачи = self.сервис.import_files([str(путь)], folder_id=self.сервис.telegram_folder_id())
        if not задачи:
            self.отправить(chat_id, м("python.telegram.trial_over"))
            return
        задача = задачи[0]
        if задача.get("status") == "failed":
            self.отправить(chat_id, м("python.telegram.not_audio"))
            return
        with self._замок:
            self._ждём_расшифровку[задача["id"]] = (chat_id, None)
        self.отправить(chat_id, м("python.telegram.accepted"))

    def _принять_ссылку(self, chat_id: int, текст: str) -> None:
        м = self.сервис._msg
        итог = self.сервис.import_link(текст, folder_id=self.сервис.telegram_folder_id())
        if not итог.get("ok"):
            if итог.get("trial"):
                self.отправить(chat_id, м("python.telegram.trial_over"))
            else:
                self.отправить(chat_id, м("python.telegram.help"))
            return
        with self._замок:
            self._ждём_расшифровку[итог["task"]["id"]] = (chat_id, None)
        self.отправить(chat_id, м("python.telegram.accepted_link"))

    # --- события программы ------------------------------------------------

    def следить(self, id_задачи: str, chat_id: int, заголовок: str) -> None:
        """Запись из папки-источника: прислать итоги, когда она расшифруется."""
        with self._замок:
            self._ждём_расшифровку[id_задачи] = (chat_id, заголовок)

    def _на_импорт(self, данные: dict[str, Any]) -> None:
        задача = (данные or {}).get("task") or {}
        with self._замок:
            ждём = self._ждём_расшифровку.get(задача.get("id"))
            if ждём is None or задача.get("status") not in ("done", "failed", "cancelled"):
                return
            del self._ждём_расшифровку[задача["id"]]
        chat_id, заголовок = ждём
        м = self.сервис._msg
        шапка = f"{заголовок}\n\n" if заголовок else ""
        встреча = задача.get("meeting_id")
        if задача["status"] != "done" or not встреча:
            self.отправить(chat_id, шапка + м("python.telegram.failed"))
            return
        текст = self.сервис.transcript_text(встреча)
        if not текст.strip():
            self.отправить(chat_id, шапка + м("python.telegram.empty"))
            return
        можно_итоги = self.сервис.telegram_can_summarize()
        if заголовок and можно_итоги:
            # Запись из папки: человек её не присылал, ему нужна суть, а не
            # получасовая расшифровка звонка в двадцати сообщениях.
            self._запросить_итоги(встреча, chat_id, заголовок)
            return
        self.отправить(chat_id, шапка + м("python.telegram.transcript") + "\n\n" + текст)
        if not можно_итоги:
            if not заголовок:
                self.отправить(chat_id, м("python.telegram.no_model"))
            return
        self._запросить_итоги(встреча, chat_id, None)

    def _запросить_итоги(self, встреча: str, chat_id: int, заголовок: str | None) -> None:
        запуск = self.сервис.generate_summary(встреча)
        if запуск.get("ok"):
            with self._замок:
                self._ждём_итоги[встреча] = (chat_id, заголовок)
        elif запуск.get("trial"):
            self.отправить(chat_id, (f"{заголовок}\n\n" if заголовок else "") + self.сервис._msg("python.telegram.summary_later"))
        else:
            # Модель занята другой встречей: дождёмся её и попробуем снова.
            with self._замок:
                self._очередь_итогов.append((встреча, chat_id, заголовок))

    def _следующие_итоги(self) -> None:
        with self._замок:
            if not self._очередь_итогов:
                return
            встреча, chat_id, заголовок = self._очередь_итогов.pop(0)
        запуск = self.сервис.generate_summary(встреча)
        if запуск.get("ok"):
            with self._замок:
                self._ждём_итоги[встреча] = (chat_id, заголовок)
        else:
            with self._замок:
                self._очередь_итогов.insert(0, (встреча, chat_id, заголовок))

    def _на_итоги(self, данные: dict[str, Any]) -> None:
        with self._замок:
            ждём = self._ждём_итоги.pop((данные or {}).get("meeting_id"), None)
        if ждём is not None:
            chat_id, заголовок = ждём
            шапка = f"{заголовок}\n\n" if заголовок else ""
            self.отправить(chat_id, шапка + self.сервис._msg("python.telegram.summary") + "\n\n"
                           + без_разметки(данные.get("summary", "")))
        self._следующие_итоги()

    def _на_ошибку_итогов(self, данные: dict[str, Any]) -> None:
        with self._замок:
            ждём = self._ждём_итоги.pop((данные or {}).get("meeting_id"), None)
        if ждём is not None:
            chat_id, заголовок = ждём
            self.отправить(chat_id, (f"{заголовок}\n\n" if заголовок else "")
                           + self.сервис._msg("python.telegram.summary_later"))
        self._следующие_итоги()

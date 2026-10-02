"""Главное окно: frameless, поверх остальных, живёт в трее.

Ключевые решения:
- `hidden=True` при создании и показ вручную — иначе окно моргает
  в дефолтной позиции до применения сохранённой геометрии.
- Закрытие окна перехватывается и превращается в скрытие: приложение
  завершается только через трей или явный выход.
- События шины транслируются во фронт через `evaluate_js`, потому что
  pywebview не умеет push из питона иначе.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import webview

from ..core import paths, settings as settings_mod
from ..core.events import (
    ANALYSIS_CHUNK,
    ANALYSIS_ERROR,
    ANALYSIS_READY,
    APP_QUIT,
    TRIAL_BLOCKED,
    TELEGRAM_CHANGED,
    CHAT_CHUNK,
    CHAT_ERROR,
    CHAT_MESSAGE,
    IMPORT_CHANGED,
    IMPORT_PROGRESS,
    MEETINGS_CHANGED,
    MEETING_UPDATED,
    MEANING_STATE,
    MODEL_DOWNLOAD,
    LLM_DOWNLOAD,
    NEW_VERSION,
    RECOGNITION_BACKFILL,
    UPDATE_STATE,
    RECORDING_LEVEL,
    RECORDING_ERROR,
    RECORDING_FOREIGN_SPEECH,
    RECORDING_SILENT,
    SYSTEM_SPEECH_NOTICED,
    RECORDING_STARTED,
    RECORDING_STOPPED,
    SUMMARY_CHUNK,
    SUMMARY_ERROR,
    SUMMARY_READY,
    SUMMARY_STATUS,
    TRANSCRIPT_DRAFT,
    TRANSCRIPT_SEGMENT,
    WINDOW_HIDE,
    WINDOW_SHOW,
    WINDOW_TOGGLE,
    bus,
)
from ..core.service import AppService
from . import win32
from .api import Api

log = logging.getLogger(__name__)

# Уровни звука летят 10 раз в секунду; гонять их через шину в JS дорого,
# поэтому подписываемся точечно и только на нужные темы.
FORWARDED_EVENTS = (
    MEETINGS_CHANGED,
    MEETING_UPDATED,
    RECORDING_STARTED,
    RECORDING_STOPPED,
    RECORDING_LEVEL,
    RECORDING_ERROR,
    RECORDING_SILENT,
    RECORDING_FOREIGN_SPEECH,
    # Сторож заметил разговор при выключенной записи. Без этой строки
    # предложение живёт только в уведомлении, а плашка в окне не
    # появляется никогда: ровно та беда, что была с черновиками в 0.7.0.
    SYSTEM_SPEECH_NOTICED,
    TRANSCRIPT_DRAFT,
    TRANSCRIPT_SEGMENT,
    MODEL_DOWNLOAD,
    # Поиск по смыслу: скачана ли модель и сколько встреч посчитано.
    # Без этой строки окно не узнало бы, что поиск стал умнее.
    MEANING_STATE,
    LLM_DOWNLOAD,
    IMPORT_CHANGED,
    IMPORT_PROGRESS,
    SUMMARY_CHUNK,
    SUMMARY_READY,
    SUMMARY_STATUS,
    SUMMARY_ERROR,
    # Разборы по методикам. Без них карточка SPIN или STAR навсегда
    # застревала бы на «Читаем расшифровку…»: модель досчитала, а окно
    # об этом не узнало.
    ANALYSIS_CHUNK,
    ANALYSIS_READY,
    ANALYSIS_ERROR,
    TRIAL_BLOCKED,
    TELEGRAM_CHANGED,
    CHAT_CHUNK,
    CHAT_MESSAGE,
    CHAT_ERROR,
    # Без этого ежедневная проверка находила новую версию, писала строчку
    # в журнал и на том всё заканчивалось: человек ничего не видел.
    NEW_VERSION,
    # Ход тихого обновления: полоса загрузки и кнопка «поставить сейчас».
    UPDATE_STATE,
    # Докатка после «стоп»: сколько кусков ещё досчитывается.
    RECOGNITION_BACKFILL,
)


class MainWindow:
    def __init__(self, service: AppService) -> None:
        self.service = service
        self.api = Api(service)
        self.window: webview.Window | None = None
        self._visible = True
        self._quitting = False
        self._lock = threading.RLock()
        self._геометрия_таймер: threading.Timer | None = None

    def create(self) -> webview.Window:
        geom = self.service.settings.window
        index = str(paths.web_dir() / "index.html")
        x, y = self._исправить_положение(geom)

        self.window = webview.create_window(
            title=self.service._msg("python.app.name"),
            url=index,
            js_api=self.api,
            width=geom.width,
            height=geom.height,
            x=x,
            y=y,
            frameless=True,
            easy_drag=False,  # таскаем сами за заголовок, иначе не выделить текст
            on_top=self.service.settings.always_on_top,
            resizable=True,
            min_size=(360, 420),
            background_color="#FAF9F7",
            hidden=self.service.settings.start_hidden,
        )
        self.api._attach(self.window)
        self._visible = not self.service.settings.start_hidden
        self.service._window_visible = self._visible

        self.window.events.closing += self._on_closing
        self.window.events.moved += self._on_geometry_changed
        self.window.events.resized += self._on_geometry_changed
        # Пути брошенных файлов знает только питон: браузеру из
        # соображений безопасности видно лишь имя. pywebview дописывает
        # настоящий путь в объект файла, но делает это, только если drop
        # слушает питон, поэтому подписка живёт здесь, а не во фронте.
        self.window.events.loaded += self._on_loaded

        self._subscribe()
        return self.window

    def _исправить_положение(self, geom) -> tuple[int | None, int | None]:
        """Вернуть окно на экран, если сохранённое место недоступно.

        Так бывает после сворачивания (Windows отдаёт -32000, -32000) и
        после отключения второго монитора. Значок в трее при этом есть,
        а окна не видно нигде, и достать его нечем.

        Правим не только на лету, но и в файле настроек: иначе при
        каждом запуске повторялась бы одна и та же починка.
        """
        экраны = win32.экраны() if win32.AVAILABLE else []
        было = (geom.x, geom.y, geom.width, geom.height)
        if not settings_mod.починить_геометрию(geom, экраны):
            return geom.x, geom.y
        log.warning(
            "Окно было потеряно (x=%s y=%s %sx%s), возвращаем на видное место "
            "(x=%s y=%s %sx%s)",
            *было, geom.x, geom.y, geom.width, geom.height,
        )
        try:
            settings_mod.save(self.service.settings)
        except Exception:
            # Не смогли записать — окно всё равно покажем как надо.
            log.debug("Не удалось сохранить исправленное положение окна", exc_info=True)
        return geom.x, geom.y

    def _on_loaded(self) -> None:
        """Подписаться на брошенные файлы, когда документ готов."""
        if self.window is None:
            return
        try:
            body = self.window.dom.get_element("body")
            if body is None:
                log.warning("Не нашли body: перетаскивание файлов не заработает")
                return
            body.events.drop += self._on_files_dropped
            log.info("Перетаскивание файлов в окно включено")
        except Exception:
            # Без этого приложение остаётся полностью рабочим, просто
            # файлы придётся выбирать кнопкой.
            log.exception("Не удалось включить перетаскивание файлов")

    def _on_files_dropped(self, event: Any) -> None:
        """Файлы бросили в окно: отдать их сервису на разбор."""
        try:
            data = (event or {}).get("dataTransfer") or {}
            files = data.get("files") or []
            paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
            skipped = len(files) - len(paths)
            if skipped:
                # Так бывает с файлами из архивов и облачных папок,
                # которых физически нет на диске.
                log.warning("У %d брошенных файлов нет пути на диске", skipped)
            if not paths:
                return
            log.info("Брошено файлов: %d", len(paths))
            self.service.import_files(paths)
        except Exception:
            log.exception("Не удалось принять брошенные файлы")

    # --- реакция на события шины ----------------------------------------

    def _subscribe(self) -> None:
        for topic in FORWARDED_EVENTS:
            bus.on(topic, self._forward_to_js)
        bus.on(WINDOW_SHOW, lambda _p: self.show())
        bus.on(WINDOW_HIDE, lambda _p: self.hide())
        bus.on(WINDOW_TOGGLE, lambda _p: self.toggle())
        bus.on(APP_QUIT, lambda _p: self.quit())

    def _forward_to_js(self, payload: dict[str, Any]) -> None:
        if self.window is None:
            return
        try:
            data = json.dumps(payload, ensure_ascii=False)
            self.window.evaluate_js(f"window.__konspekt_event && window.__konspekt_event({data})")
        except Exception:
            # Окно могло уже закрыться — это не повод падать.
            log.debug("Не удалось доставить событие во фронт", exc_info=True)

    # --- управление видимостью -------------------------------------------

    def show(self) -> None:
        with self._lock:
            if self.window is None:
                return
            try:
                # Сохранённое место могло стать недоступным, пока окно
                # было спрятано: например, отключили второй монитор.
                self._вернуть_если_потеряно()
                self.window.show()
                # Одного show() мало: свёрнутое окно от него не
                # разворачивается, и человек по-прежнему ничего не видит.
                if win32.AVAILABLE:
                    win32.показать(self.window)
                self._visible = True
                self.service._window_visible = True
                log.info("Окно показано")
            except Exception:
                log.exception("Не удалось показать окно")

    def _вернуть_если_потеряно(self) -> None:
        """Переставить окно на экран, если оно оказалось вне его."""
        if self.window is None or not win32.AVAILABLE:
            return
        try:
            rect = win32.get_rect(self.window)
            if rect is None:
                return
            x, y, width, height = rect
            экраны = win32.экраны()
            if settings_mod.геометрия_годится(x, y, width, height, экраны):
                return
            нx, нy = settings_mod.поставить_по_центру(width, height, экраны)
            if нx is None or нy is None:
                return
            log.warning("Окно было вне экрана (x=%s y=%s), возвращаем", x, y)
            win32.set_geometry(self.window, нx, нy, width, height)
            self.service.save_window_geometry(нx, нy, width, height)
        except Exception:
            log.debug("Не удалось вернуть окно на экран", exc_info=True)

    def hide(self) -> None:
        with self._lock:
            if self.window is None:
                return
            try:
                self.window.hide()
                self._visible = False
                # Обновление ждёт, когда человек уйдёт из программы.
                self.service._window_visible = False
                log.info("Окно скрыто")
            except Exception:
                log.exception("Не удалось скрыть окно")

    def toggle(self) -> None:
        log.info("Переключение окна, сейчас видимо=%s", self._visible)
        if self._visible:
            self.hide()
        else:
            self.show()

    @property
    def is_visible(self) -> bool:
        return self._visible

    def quit(self) -> None:
        with self._lock:
            self._quitting = True
        # Окно растянули и сразу вышли: место ещё ждёт паузы. Запоминаем
        # сейчас, пока окно живо, иначе следующий запуск откроет старый размер.
        будильник = self._геометрия_таймер
        if будильник is not None and будильник.is_alive():
            будильник.cancel()
            self._сохранить_геометрию()
        try:
            if self.window:
                self.window.destroy()
        except Exception:
            log.debug("Окно уже уничтожено", exc_info=True)

    # --- обработчики окна -------------------------------------------------

    def _on_closing(self) -> bool:
        """Крестик прячет окно в трей вместо выхода из приложения."""
        if self._quitting:
            return True
        self.hide()
        return False

    # Сколько ждать тишины после последнего шага, прежде чем записать место.
    ГЕОМЕТРИЯ_ПАУЗА = 0.4

    def _on_geometry_changed(self, *_args: Any) -> None:
        """Окно двигают или растягивают: запомнить место, когда остановятся.

        Событие приходит на каждый шаг, десятки раз за одно движение мыши.
        Раньше каждый шаг читал координаты у окна и писал settings.json на
        диск, и растягивание окна во время записи подтормаживало: диск и
        так занят записью звука. Теперь шаги только переводят будильник, а
        место запоминается один раз, через паузу после последнего шага.
        """
        if self.window is None:
            return
        старый = self._геометрия_таймер
        if старый is not None:
            старый.cancel()
        будильник = threading.Timer(self.ГЕОМЕТРИЯ_ПАУЗА, self._сохранить_геометрию)
        будильник.daemon = True
        self._геометрия_таймер = будильник
        будильник.start()

    def _сохранить_геометрию(self) -> None:
        """Запомнить место окна.

        Сворачивание тоже приходит сюда, но координатами -32000, -32000:
        так Windows обозначает свёрнутое окно. Записать их означало бы
        при следующем запуске создать окно за пределами экрана, поэтому
        такие сообщения пропускаем. Само значение отсеивает
        `save_window_geometry`, здесь же не трогаем и размер.
        """
        if self.window is None:
            return
        try:
            if win32.AVAILABLE and win32.свёрнуто(self.window):
                return
            self.api.save_geometry(
                self.window.x, self.window.y, self.window.width, self.window.height
            )
        except Exception:
            log.debug("Не удалось сохранить геометрию окна", exc_info=True)

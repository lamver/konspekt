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

from ..core import paths
from ..core.events import (
    APP_QUIT,
    MEETINGS_CHANGED,
    MEETING_UPDATED,
    MODEL_DOWNLOAD,
    RECORDING_LEVEL,
    RECORDING_ERROR,
    RECORDING_STARTED,
    RECORDING_STOPPED,
    TRANSCRIPT_SEGMENT,
    WINDOW_HIDE,
    WINDOW_SHOW,
    WINDOW_TOGGLE,
    bus,
)
from ..core.service import AppService
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
    TRANSCRIPT_SEGMENT,
    MODEL_DOWNLOAD,
)


class MainWindow:
    def __init__(self, service: AppService) -> None:
        self.service = service
        self.api = Api(service)
        self.window: webview.Window | None = None
        self._visible = True
        self._quitting = False
        self._lock = threading.RLock()

    def create(self) -> webview.Window:
        geom = self.service.settings.window
        index = str(paths.web_dir() / "index.html")

        self.window = webview.create_window(
            title="Konspekt",
            url=index,
            js_api=self.api,
            width=geom.width,
            height=geom.height,
            x=geom.x,
            y=geom.y,
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

        self.window.events.closing += self._on_closing
        self.window.events.moved += self._on_geometry_changed
        self.window.events.resized += self._on_geometry_changed

        self._subscribe()
        return self.window

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
                self.window.show()
                self._visible = True
                log.info("Окно показано")
            except Exception:
                log.exception("Не удалось показать окно")

    def hide(self) -> None:
        with self._lock:
            if self.window is None:
                return
            try:
                self.window.hide()
                self._visible = False
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

    def _on_geometry_changed(self, *_args: Any) -> None:
        if self.window is None:
            return
        try:
            self.api.save_geometry(
                self.window.x, self.window.y, self.window.width, self.window.height
            )
        except Exception:
            log.debug("Не удалось сохранить геометрию окна", exc_info=True)

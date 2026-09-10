"""Иконка в трее на pystray.

pystray требует собственный цикл событий, а pywebview занимает главный поток,
поэтому трей живёт в фоновом потоке и общается с окном только
через шину событий.
"""

from __future__ import annotations

import logging
import threading

import pystray
from PIL import Image, ImageDraw

from ..core.events import (
    APP_QUIT,
    RECORDING_FOREIGN_SPEECH,
    RECORDING_STARTED,
    RECORDING_STOPPED,
    WINDOW_SHOW,
    WINDOW_TOGGLE,
    bus,
)

log = logging.getLogger(__name__)

ACCENT = (214, 90, 63)      # терракота, цвет акцента в UI
INK = (43, 41, 38)          # основной тёмный
REC = (220, 53, 69)         # красный индикатор записи


def _make_icon(recording: bool = False, size: int = 64) -> Image.Image:
    """Рисуем иконку кодом, чтобы не тащить бинарные ассеты в репо.

    Размер параметром: из этой же функции делается .ico для ярлыка и
    установщика, а там нужны все размеры от 16 до 256. Рисуем в четыре раза
    крупнее и уменьшаем, иначе на мелких размерах края лесенкой.
    """
    scale = 4
    box = size * scale
    img = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k = box / 64  # пропорции подобраны на 64 пикселях

    # Страница блокнота
    d.rounded_rectangle([10 * k, 6 * k, 54 * k, 58 * k], radius=8 * k, fill=INK)
    d.rounded_rectangle([10 * k, 6 * k, 20 * k, 58 * k], radius=8 * k, fill=ACCENT)
    # Строки текста
    for y in (20, 30, 40):
        d.rounded_rectangle(
            [26 * k, y * k, 48 * k, (y + 4) * k], radius=2 * k, fill=(250, 249, 247)
        )

    if recording:
        d.ellipse([38 * k, 38 * k, 60 * k, 60 * k], fill=REC)

    return img.resize((size, size), Image.LANCZOS)


class TrayIcon:
    def __init__(self, on_new_meeting=None) -> None:
        self._on_new_meeting = on_new_meeting
        self._recording = False
        self._icon = pystray.Icon(
            "konspekt",
            icon=_make_icon(False),
            title="Konspekt",
            menu=self._build_menu(),
        )
        self._thread: threading.Thread | None = None
        bus.on(RECORDING_STARTED, lambda _p: self._set_recording(True))
        bus.on(RECORDING_STOPPED, lambda _p: self._set_recording(False))
        # Вопрос про чужой звук в системе. Окно во время встречи обычно
        # свёрнуто в трей, и плашка внутри него никого не спасёт: человек
        # увидит её через час, когда ролик уже уехал в расшифровку.
        # Поэтому дублируем системным уведомлением — его видно поверх
        # любого окна.
        bus.on(RECORDING_FOREIGN_SPEECH, self._спросить_про_звук)

    def _спросить_про_звук(self, payload: dict) -> None:
        """Показать системное уведомление о чужой речи в системном звуке.

        Отвечать надо прямо отсюда, не открывая окно: человек посреди
        встречи, и заставлять его искать программу ради одного щелчка
        значит сделать вопрос бесполезным.

        Сначала пробуем тост Windows — он умеет кнопки. Если не вышло
        (не та система, отключены уведомления), падаем на обычное
        «облачко» трея: оно без кнопок, но хотя бы предупредит.
        """
        from ..ui import уведомления

        # Два разных вопроса. Звук пишется — спрашиваем, не выключить ли
        # (вдруг это чужой ролик). Не пишется — предлагаем включить: мы
        # прямо сейчас теряем собеседника, и молчать об этом хуже всего.
        пишется = payload.get("пишется", True)
        if пишется:
            текст = ("В системном звуке слышен разговор. Если это ролик "
                     "или музыка, а не собеседник, его лучше не писать.")
            кнопка1, ответ1 = "Не писать", "off"
            кнопка2, ответ2 = "Это собеседник", "keep"
        else:
            текст = ("В системном звуке слышен разговор, а он не "
                     "записывается. Похоже, собеседника сейчас не слышно "
                     "в расшифровке.")
            кнопка1, ответ1 = "Писать собеседника", "on"
            кнопка2, ответ2 = "Не нужно", "keep"

        показан = False
        try:
            показан = уведомления.показать_вопрос(
                заголовок="Konspekt",
                текст=текст,
                кнопка1=кнопка1,
                ответ1=ответ1,
                кнопка2=кнопка2,
                ответ2=ответ2,
            )
        except Exception:
            log.debug("Тост с кнопками не вышел", exc_info=True)

        if показан:
            return

        try:
            self._icon.notify(текст + " Откройте Konspekt, чтобы ответить.",
                              "Konspekt")
        except Exception:
            # Уведомления есть не на всякой системе, и это не повод
            # ронять запись: плашка в окне всё равно покажется.
            log.debug("Не удалось показать системное уведомление", exc_info=True)

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "Показать Konspekt",
                lambda: bus.emit(WINDOW_SHOW),
                default=True,
            ),
            pystray.MenuItem("Скрыть / показать", lambda: bus.emit(WINDOW_TOGGLE)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Новая встреча", self._new_meeting),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda: bus.emit(APP_QUIT)),
        )

    def _new_meeting(self) -> None:
        if self._on_new_meeting:
            self._on_new_meeting()
        bus.emit(WINDOW_SHOW)

    def _set_recording(self, recording: bool) -> None:
        if self._recording == recording:
            return
        self._recording = recording
        try:
            self._icon.icon = _make_icon(recording)
            self._icon.title = "Konspekt — идёт запись" if recording else "Konspekt"
        except Exception:
            log.debug("Не удалось обновить иконку трея", exc_info=True)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            log.debug("Трей уже остановлен", exc_info=True)

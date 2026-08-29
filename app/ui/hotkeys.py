"""Глобальная горячая клавиша показать/скрыть окно.

Почему не `pynput.keyboard.GlobalHotKeys`: при зажатом Ctrl Windows отдаёт
не букву, а управляющий символ (Ctrl+K приходит как `\\x0b`). Штатный
матчер pynput сравнивает его с ожидаемым `k` и никогда не совпадает,
поэтому хоткей молча не срабатывает. Здесь символ нормализуется обратно
в букву по правилу `chr(ord(c) + 96)`.

Горячая клавиша — удобство, а не обязательная часть: если система не дала
её перехватить (нет прав, нет X11), приложение обязано продолжить работать.
"""

from __future__ import annotations

import logging

from ..core.events import WINDOW_TOGGLE, bus

log = logging.getLogger(__name__)


def _parse(combo: str) -> tuple[set[str], str]:
    """Разбирает строку вида `<ctrl>+<shift>+k` в модификаторы и клавишу."""
    mods: set[str] = set()
    key = ""
    for part in combo.lower().split("+"):
        part = part.strip()
        if part.startswith("<") and part.endswith(">"):
            mods.add(part[1:-1])
        elif part:
            key = part
    return mods, key


class HotkeyManager:
    def __init__(self, combo: str = "<ctrl>+<shift>+k") -> None:
        self.combo = combo
        self._mods, self._key = _parse(combo)
        self._listener = None
        self._pressed: set[str] = set()
        self._fired = False

    # --- нормализация клавиш ---------------------------------------------

    @staticmethod
    def _mod_name(key) -> str | None:
        name = getattr(key, "name", "") or ""
        for mod in ("ctrl", "shift", "alt", "cmd"):
            if name.startswith(mod):
                return mod
        return None

    @staticmethod
    def _char(key) -> str | None:
        """Возвращает букву клавиши, разворачивая control-символы."""
        char = getattr(key, "char", None)
        if not char:
            return None
        # Ctrl+K приходит как \x0b: 0x0b + 96 == ord('k').
        if len(char) == 1 and ord(char) < 32:
            return chr(ord(char) + 96)
        return char.lower()

    # --- обработчики -------------------------------------------------------

    def _on_press(self, key) -> None:
        mod = self._mod_name(key)
        if mod:
            self._pressed.add(mod)
            return

        char = self._char(key)
        if char != self._key or not self._mods.issubset(self._pressed):
            return
        # Автоповтор клавиши не должен переключать окно десять раз подряд.
        if self._fired:
            return
        self._fired = True
        log.info("Сработала горячая клавиша %s", self.combo)
        bus.emit(WINDOW_TOGGLE)

    def _on_release(self, key) -> None:
        mod = self._mod_name(key)
        if mod:
            self._pressed.discard(mod)
        elif self._char(key) == self._key:
            self._fired = False

    # --- жизненный цикл -----------------------------------------------------

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:
            log.warning("pynput недоступен, горячая клавиша отключена")
            return False
        try:
            self._listener = keyboard.Listener(
                on_press=self._on_press, on_release=self._on_release
            )
            self._listener.daemon = True
            self._listener.start()
            log.info("Горячая клавиша %s активна", self.combo)
            return True
        except Exception:
            log.warning("Не удалось зарегистрировать горячую клавишу %s", self.combo, exc_info=True)
            return False

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                log.debug("Слушатель горячих клавиш уже остановлен", exc_info=True)
            self._listener = None

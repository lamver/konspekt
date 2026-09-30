# -*- coding: utf-8 -*-
"""Вид копирования и свёрнутые заметки переживают перезапуск.

Без этого человек, который вставляет заметки в почту, при каждом
запуске снова получал бы звёздочки и решётки, а свёрнутое саммари
разворачивалось бы на пол-окна.
"""
from __future__ import annotations

import testenv  # noqa: F401  песочница вместо боевого профиля

from app.core import settings as settings_mod
from app.core.service import AppService

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


проверить(settings_mod.Settings().copy_mode == "markdown", "по умолчанию копируем с разметкой")
проверить(settings_mod.Settings().summary_collapsed is False, "по умолчанию заметки развёрнуты")

сервис = AppService.__new__(AppService)
сервис.settings = settings_mod.Settings()

проверить(сервис.set_copy_mode("plain") == "plain", "выбор «простым текстом» принят")
проверить(settings_mod.load().copy_mode == "plain", "выбор «простым текстом» пережил перезапуск")
проверить(сервис.set_copy_mode("html") == "markdown", "незнакомый вид не записывается как есть")
проверить(settings_mod.load().copy_mode == "markdown", "после мусора в файле снова разметка, а не пустота")

проверить(сервис.set_summary_collapsed(True) is True, "свернуть заметки")
проверить(settings_mod.load().summary_collapsed is True, "свёрнутые заметки пережили перезапуск")
проверить(сервис.set_summary_collapsed(0) is False, "развернуть заметки")
проверить(settings_mod.load().summary_collapsed is False, "развёрнутые заметки пережили перезапуск")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Вид копирования и свёрнутость заметок запоминаются.")

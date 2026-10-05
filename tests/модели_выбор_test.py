# -*- coding: utf-8 -*-
"""Смена своей модели доходит до сервера модели.

Беда, которой не должно быть: человек выбрал умную модель, а отвечает
по-прежнему быстрая, потому что сервер с ней уже поднят. Или выбор не
записался, и после перезапуска снова быстрая.
"""
from __future__ import annotations

import testenv  # noqa: F401  песочница вместо боевого профиля

import tempfile
from pathlib import Path

from app.core import settings as settings_mod
from app.core.service import AppService
from app.llm import local as local_mod
from app.llm.manager import LlmManager

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


class ПоддельныйСервер:
    поднято: list[Path] = []

    def __init__(self, model_path, binary=None):
        self.model_path = Path(model_path)
        self.остановлен = False

    @property
    def is_running(self):
        return not self.остановлен

    def ensure_started(self):
        type(self).поднято.append(self.model_path)
        return "http://127.0.0.1:1/v1"

    def stop(self):
        self.остановлен = True


with tempfile.TemporaryDirectory() as врем:
    корень = Path(врем)
    local_mod.paths.models_dir = lambda: корень  # type: ignore[assignment]
    import app.llm.manager as manager_mod
    manager_mod.LocalServer = ПоддельныйСервер  # type: ignore[assignment]
    manager_mod.default_binary = lambda: корень  # движок «есть»

    for код, о in local_mod.LOCAL_MODELS.items():
        (корень / "llm" / о["dir"]).mkdir(parents=True)
        (корень / "llm" / о["dir"] / о["file"]).write_bytes(b"x")

    сервис = AppService.__new__(AppService)
    сервис.settings = settings_mod.Settings()
    сервис.llm = LlmManager(lambda: сервис.settings.llm)

    сервис.llm.client()
    проверить(ПоддельныйСервер.поднято[-1].name == local_mod.LOCAL_MODELS["fast"]["file"],
              "по умолчанию поднимается быстрая модель")

    ст = сервис.save_llm_settings(local_model="smart")
    проверить(ст["local_model"] == "smart", "выбор умной модели принят")
    проверить(settings_mod.load().llm.local_model == "smart", "выбор модели пережил перезапуск")
    сервис.llm.client()
    проверить(ПоддельныйСервер.поднято[-1].name == local_mod.LOCAL_MODELS["smart"]["file"],
              "после смены отвечает выбранная модель, а не прежняя")

    # Смена, пока сервер жив, но без shutdown в save_llm_settings: сам
    # менеджер тоже должен заметить, что модель другая.
    сервис.settings.llm.local_model = "strong"
    старый = сервис.llm._server
    сервис.llm.client()
    проверить(старый.остановлен, "сервер с прежней моделью гасится, память не занята дважды")
    проверить(ПоддельныйСервер.поднято[-1].name == local_mod.LOCAL_MODELS["strong"]["file"],
              "поднимается мощная модель")

    ст = сервис.save_llm_settings(local_model="gpt-100500")
    проверить(ст["local_model"] == "fast" and settings_mod.load().llm.local_model == "fast",
              "незнакомая модель не записывается, откат к быстрой")

    проверить(isinstance(ст.get("ram_gb"), float) and ст["ram_gb"] > 0,
              f"программа знает, сколько памяти у компьютера: {ст.get('ram_gb')}")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Смена модели доходит до сервера и переживает перезапуск.")

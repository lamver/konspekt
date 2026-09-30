"""Проверка докачки весов модели по требованию.

Настоящие полтора гигабайта здесь качать нельзя, поэтому проверяем то,
что ломается на самом деле: что первый запрос сам зовёт загрузку, что
второй её не запускает повторно, что проценты доезжают до окна и что
одновременные запросы не качают один файл в два потока.
"""

from __future__ import annotations

import testenv  # noqa: F401  русский вывод в консоли Windows

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.core.events import LLM_DOWNLOAD, bus
from app.core.settings import LlmSettings
from app.llm import local as local_mod
from app.llm.manager import LlmManager


class FakeDownloader:
    """Загрузчик, который пишет крохотный файл вместо модели."""

    def __init__(self, dest: Path) -> None:
        self.dest = dest
        self.calls = 0
        self.is_running = False
        self.overlap = False
        self._busy = False

    def run_blocking(self, on_progress=None) -> None:
        # Две загрузки одного файла разом это склейка вместо модели.
        if self._busy:
            self.overlap = True
        self._busy = True
        self.calls += 1
        self.dest.mkdir(parents=True, exist_ok=True)
        for done in (500, 1000):
            if on_progress:
                on_progress("model.gguf", done, 1000)
            time.sleep(0.05)
        (self.dest / getattr(self, "name", local_mod.MODEL_FILE)).write_bytes(b"x")
        self._busy = False

    def downloaded_bytes(self) -> int:
        return 0

    def cancel(self) -> None:
        pass


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Подменяем каталог моделей: боевой трогать нельзя.
        local_mod.paths.models_dir = lambda: root  # type: ignore[assignment]

        cfg = LlmSettings(backend="local")
        manager = LlmManager(lambda: cfg)
        dest = root / "llm" / local_mod.MODEL_DIR_NAME
        fake = FakeDownloader(dest)
        manager.downloader = fake

        events: list[dict] = []
        bus.on(LLM_DOWNLOAD, events.append)

        # Модели нет: первый запрос должен её привезти.
        assert local_mod.find_model() is None, "каталог должен быть пуст"
        percents: list[int] = []
        manager.ensure_model(lambda n, d, t: percents.append(d * 100 // t))
        assert fake.calls == 1, f"загрузка не запустилась: {fake.calls}"
        assert local_mod.find_model() is not None, "модель не появилась"
        assert percents == [50, 100], f"прогресс не доехал: {percents}"
        print("[ok] первый запрос сам качает модель и отдаёт прогресс")

        # Второй запрос ничего не качает: файл уже на диске.
        manager.ensure_model()
        assert fake.calls == 1, "модель скачалась повторно"
        print("[ok] готовая модель второй раз не качается")

        # Одновременные запросы: саммари и чат могут прийти вместе.
        (dest / local_mod.MODEL_FILE).unlink()
        threads = [threading.Thread(target=manager.ensure_model) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not fake.overlap, "один файл качался в два потока"
        assert fake.calls == 2, f"лишние загрузки: {fake.calls}"
        print("[ok] одновременные запросы качают модель один раз")

        # Облачный бэкенд не должен тянуть локальные веса.
        (dest / local_mod.MODEL_FILE).unlink()
        cfg.backend = "remote"
        manager.ensure_model()
        assert fake.calls == 2, "облачный бэкенд полез качать локальную модель"
        print("[ok] под облаком локальные веса не качаются")

        # --- выбор модели ---------------------------------------------------
        #
        # Выбрали умную, а быстрая уже лежит: отвечать должна умная, и
        # качать надо её, а не считать, что модель уже есть.
        cfg.backend = "local"
        (dest / local_mod.MODEL_FILE).write_bytes(b"x")
        cfg.local_model = "smart"
        умная = local_mod.LOCAL_MODELS["smart"]
        fake_smart = FakeDownloader(root / "llm" / умная["dir"])
        fake_smart.name = умная["file"]
        manager.downloader = fake_smart
        assert manager.status()["model_ready"] is False, \
            "выбрана умная, а статус «готово» по лежащей быстрой"
        manager.ensure_model()
        assert fake_smart.calls == 1, "выбранная умная модель не качается"
        assert local_mod.find_model("smart") is not None, "умная модель не легла на место"
        st = manager.status()
        assert st["local_model"] == "smart" and st["model_ready"], st
        скачано = {m["code"]: m["downloaded"] for m in st["local_models"]}
        assert скачано == {"fast": True, "smart": True, "strong": False}, скачано
        print("[ok] выбранная модель качается своя, а не подменяется уже лежащей")

        cfg.local_model = "что-то странное"
        assert manager.tier == "fast", "незнакомая модель не откатилась к быстрой"
        print("[ok] незнакомый выбор модели откатывается к быстрой")

    print("\nДокачка модели работает.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

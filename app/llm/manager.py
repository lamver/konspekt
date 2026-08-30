"""Кто отвечает на запросы: локальная модель или чужой сервер.

Смысл этого слоя в том, чтобы остальное приложение не знало разницы.
Локальная модель поднимается процессом llama.cpp и говорит на том же
языке, что и облачный провайдер, поэтому выше по стеку везде один и
тот же `LlmClient`.

Сервер поднимается лениво и только под локальный бэкенд: держать в
памяти полтора гигабайта ради человека, который выбрал облако или
вообще отключил синтез, незачем.
"""

from __future__ import annotations

import logging
import threading

from ..asr.download import ModelDownloader
from .client import LlmClient, LlmError
from .local import (
    MODEL_DIR_NAME,
    MODEL_FILE,
    MODEL_REPO,
    MODEL_TOTAL_BYTES,
    LocalServer,
    default_binary,
    find_model,
    llm_dir,
)

log = logging.getLogger(__name__)

# Локальному серверу имя модели безразлично, но пустое поле некоторые
# сборки отвергают, поэтому подставляем заглушку.
LOCAL_MODEL_NAME = "local"


class LlmManager:
    """Единая точка доступа к модели.

    Отдаёт готовый клиент по текущим настройкам, а под локальный бэкенд
    сначала поднимает сервер. Настройки читаются на каждый запрос: человек
    мог переключить бэкенд, и следующий вопрос должен уйти уже туда.
    """

    def __init__(self, settings_provider) -> None:
        # Функция, а не снимок: настройки живут в AppService и меняются
        # без нашего ведома.
        self._settings = settings_provider
        self._server: LocalServer | None = None
        self._lock = threading.RLock()
        # Веса приезжают по требованию: полтора гигабайта в установщике
        # ради человека, который выберет облако, никому не нужны.
        self.downloader = ModelDownloader(
            MODEL_REPO, (MODEL_FILE,), llm_dir() / MODEL_DIR_NAME
        )
        # Саммари и чат могут попроситься одновременно: качать один файл
        # в два потока значит получить склейку вместо модели.
        self._download_lock = threading.RLock()

    # --- состояние -------------------------------------------------------

    @property
    def backend(self) -> str:
        return (self._settings().backend or "null").strip()

    @property
    def enabled(self) -> bool:
        return self.backend != "null"

    def status(self) -> dict:
        """Что показать в настройках, не поднимая сервер."""
        cfg = self._settings()
        model = find_model()
        return {
            "backend": self.backend,
            "enabled": self.enabled,
            "model_file": model.name if model else "",
            "model_ready": model is not None,
            "engine_ready": default_binary().exists(),
            "server_running": self._server is not None and self._server.is_running,
            "downloading": self.downloader.is_running,
            "bytes": self.downloader.downloaded_bytes(),
            "total_bytes": MODEL_TOTAL_BYTES,
            "base_url": cfg.base_url,
            "model": cfg.model,
            "template": cfg.template,
            "auto_summary": cfg.auto_summary,
            "has_key": bool(cfg.api_key),
        }

    # --- клиент ----------------------------------------------------------

    def client(self) -> LlmClient:
        """Клиент к текущему бэкенду. Поднимает локальный сервер, если надо."""
        cfg = self._settings()
        backend = self.backend

        if backend == "null":
            raise LlmError("Синтез выключен в настройках")

        if backend == "remote":
            if not cfg.base_url.strip():
                raise LlmError("Не указан адрес сервера модели")
            return LlmClient(
                base_url=cfg.base_url.strip(),
                api_key=cfg.api_key,
                model=cfg.model or "",
            )

        return LlmClient(base_url=self._local_url(), model=LOCAL_MODEL_NAME)

    def _local_url(self) -> str:
        with self._lock:
            model = find_model()
            if model is None:
                raise LlmError(
                    "Локальная модель не скачана. Скачайте её в настройках."
                )
            if not default_binary().exists():
                raise LlmError(
                    "Не найден движок llama.cpp. Переустановите приложение."
                )
            if self._server is None:
                self._server = LocalServer(model)
            try:
                return self._server.ensure_started()
            except Exception as exc:
                # Наружу отдаём одну понятную ошибку: тексты RuntimeError
                # из запуска процесса человеку ничего не говорят.
                log.exception("Не удалось поднять локальную модель")
                raise LlmError(f"Локальная модель не запустилась: {exc}") from exc

    # --- веса ------------------------------------------------------------

    def download(
        self,
        on_progress=None,
        on_done=None,
    ) -> None:
        """Скачать веса в фоне. Уже скачанные не трогаем."""
        if find_model() is not None or self.downloader.is_running:
            return
        self.downloader.start(on_progress, on_done)

    def cancel_download(self) -> None:
        self.downloader.cancel()

    def ensure_model(self, on_progress=None) -> None:
        """Дождаться весов, скачав их при необходимости.

        Человек нажал «Сделать заметки», а модели на диске нет. Отправлять
        его в настройки за отдельной кнопкой значит ломать работу на
        ровном месте: качаем прямо здесь и говорим, сколько осталось.
        """
        if self.backend != "local" or find_model() is not None:
            return
        with self._download_lock:
            if find_model() is not None:
                return
            self.downloader.run_blocking(on_progress)

    def shutdown(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.stop()
                self._server = None

"""Локальная модель через llama.cpp.

Считаем не сами: llama.cpp умеет отдавать сервер в формате OpenAI,
поэтому локальная модель для остального приложения выглядит ровно так
же, как наш сервер или чужой провайдер. Один клиент на всё.

Сервер поднимается отдельным процессом и живёт, пока живёт приложение.
Порт занимаем свободный: фиксированный рано или поздно окажется занят
чужой программой, и это будет непонятная ошибка.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx

from ..core import paths

log = logging.getLogger(__name__)

# Сколько ждём, пока сервер прогрузит веса и начнёт отвечать. Модель на
# 1.7 ГБ читается с диска несколько секунд, на медленном диске дольше.
STARTUP_TIMEOUT = 120.0

# Размер окна контекста. Часовая встреча это примерно 20-25 тысяч
# токенов, поэтому 32k выбраны с запасом. Память под контекст выделяется
# сразу, и на слабой машине большее окно будет лишним расходом.
CONTEXT_SIZE = 32768

# Какую модель качаем по требованию.
#
# Qwen3 1.7B в Q8: 1.8 ГБ, разбирает часовую встречу на процессоре за
# несколько минут и хорошо понимает русский. Класть её в дистрибутив
# нельзя, установщик раздулся бы вдесятеро, поэтому она приезжает при
# первом обращении, как и веса распознавания.
MODEL_REPO = "Qwen/Qwen3-1.7B-GGUF"
MODEL_FILE = "Qwen3-1.7B-Q8_0.gguf"
MODEL_DIR_NAME = "qwen3-1.7b"
MODEL_TOTAL_BYTES = 1_834_426_016


class LocalServer:
    """Процесс llama.cpp с загруженной моделью.

    Запуск ленивый: пока пользователь не попросил саммари, полтора
    гигабайта в памяти держать незачем.
    """

    def __init__(self, model_path: str | Path, binary: str | Path | None = None) -> None:
        self._model = Path(model_path)
        self._binary = Path(binary) if binary else default_binary()
        self._proc: subprocess.Popen | None = None
        self._port = 0
        self._lock = threading.RLock()

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/v1"

    def ensure_started(self) -> str:
        """Поднять сервер, если он ещё не поднят. Вернуть адрес API."""
        with self._lock:
            if self.is_running:
                return self.base_url
            self._start()
            return self.base_url

    def _start(self) -> None:
        if not self._model.exists():
            raise RuntimeError(f"Файл модели не найден: {self._model}")
        if not self._binary.exists():
            raise RuntimeError(
                "Не найден llama-server. Скачайте локальный движок в настройках."
            )

        self._port = _free_port()
        cmd = [
            str(self._binary),
            "--model", str(self._model),
            "--host", "127.0.0.1",
            "--port", str(self._port),
            "--ctx-size", str(CONTEXT_SIZE),
            # Выгружаем на видеокарту всё, что влезет. Без видеокарты
            # параметр просто игнорируется, отдельная ветка не нужна.
            "--n-gpu-layers", "999",
            # Прогрев пустым запросом добавляет секунды к запуску, а
            # первый настоящий запрос всё равно ждём с индикатором.
            "--no-warmup",
        ]

        log.info("Запускаем llama-server на порту %s", self._port)
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_no_window(),
        )
        try:
            self._wait_ready()
        except Exception:
            self.stop()
            raise

    def _wait_ready(self) -> None:
        """Дождаться, пока сервер начнёт отвечать.

        Опрашиваем `/health`: он отвечает 503, пока модель грузится, и
        200, когда готова.
        """
        deadline = time.monotonic() + STARTUP_TIMEOUT
        url = f"http://127.0.0.1:{self._port}/health"
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError("Локальный сервер модели неожиданно завершился")
            try:
                resp = httpx.get(url, timeout=2.0)
                if resp.status_code == 200:
                    log.info("Локальная модель готова")
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError("Локальная модель не успела загрузиться")

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            if proc is None or proc.poll() is not None:
                return
            log.info("Останавливаем локальный сервер модели")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Сервер, застрявший на выгрузке весов, не должен мешать
                # приложению закрыться.
                proc.kill()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _no_window() -> int:
    """Не показывать окно консоли при запуске сервера."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def llm_dir() -> Path:
    return paths.models_dir() / "llm"


def engine_dir() -> Path:
    return llm_dir() / "engine"


def default_binary() -> Path:
    name = "llama-server.exe" if os.name == "nt" else "llama-server"
    return engine_dir() / name


def find_model() -> Path | None:
    """Найти скачанную модель. Берём первый .gguf, какой лежит."""
    root = llm_dir()
    if not root.exists():
        return None
    files = sorted(root.rglob("*.gguf"))
    return files[0] if files else None

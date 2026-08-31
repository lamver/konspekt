"""Локальная модель через llama.cpp.

Считаем не сами: llama.cpp умеет отдавать сервер в формате OpenAI,
поэтому локальная модель для остального приложения выглядит ровно так
же, как наш сервер или чужой провайдер. Один клиент на всё.

Сервер поднимается отдельным процессом и живёт, пока живёт приложение.
Порт занимаем свободный: фиксированный рано или поздно окажется занят
чужой программой, и это будет непонятная ошибка.

«Пока живёт приложение» держится не на честном слове: на Windows сервер
привязан к job-объекту, который система закрывает вместе с нами. Иначе
при падении или снятии задачи llama-server остаётся в памяти и держит
несколько гигабайт до перезагрузки, а следующий запуск поднимает ещё
один такой же.
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

# Job-объект Windows: всё, что в него положено, система прибивает,
# когда закрывается последний держатель. Создаётся один на процесс.
_job_handle = None
_job_lock = threading.Lock()

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
            creationflags=_no_window() | _kill_job(),
        )
        # Сразу после запуска: чтобы сервер не пережил нас, даже если
        # приложение снимут задачей до первого ответа модели.
        _attach_to_job(self._proc)
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


def _kill_job() -> int:
    """Флаг, позволяющий положить процесс в job-объект.

    Без CREATE_BREAKAWAY_FROM_JOB дочерний процесс наследует чужой job,
    если приложение само запущено внутри такого (так делают некоторые
    оболочки и отладчики), и добавить его в наш уже нельзя.
    """
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)


def _attach_to_job(proc: subprocess.Popen) -> None:
    """Привязать процесс к жизни нашего.

    Windows закрывает job-объект вместе с последним держателем, а с ним
    и все процессы внутри. Так llama-server не переживёт ни закрытие
    приложения, ни его падение, ни снятие задачи.

    Молча ничего не делаем, если не вышло: неубитый сервер это утечка
    памяти, но не повод отказать человеку в заметках.
    """
    if os.name != "nt":
        return

    global _job_handle
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Без restype ctypes считает результат int, и 64-битный HANDLE
        # молча теряет старшую половину. Ошибка при этом не возникает,
        # просто перестаёт работать.
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE

        with _job_lock:
            if _job_handle is None:
                handle = kernel32.CreateJobObjectW(None, None)
                if not handle:
                    return

                # JOBOBJECT_EXTENDED_LIMIT_INFORMATION целиком нам не
                # нужен, важен единственный флаг KILL_ON_JOB_CLOSE в
                # начале структуры. Размер берём с запасом.
                class _BasicLimits(ctypes.Structure):
                    _fields_ = [
                        ("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD),
                    ]

                class _IoCounters(ctypes.Structure):
                    _fields_ = [(name, ctypes.c_uint64) for name in (
                        "ReadOperationCount", "WriteOperationCount",
                        "OtherOperationCount", "ReadTransferCount",
                        "WriteTransferCount", "OtherTransferCount",
                    )]

                class _ExtendedLimits(ctypes.Structure):
                    _fields_ = [
                        ("BasicLimitInformation", _BasicLimits),
                        ("IoInfo", _IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t),
                    ]

                info = _ExtendedLimits()
                info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
                if not kernel32.SetInformationJobObject(
                    handle, 9, ctypes.byref(info), ctypes.sizeof(info)
                ):
                    kernel32.CloseHandle(handle)
                    return
                _job_handle = handle

        # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        process = kernel32.OpenProcess(0x0100 | 0x0001, False, proc.pid)
        if not process:
            return
        try:
            kernel32.AssignProcessToJobObject(_job_handle, process)
        finally:
            kernel32.CloseHandle(process)
    except Exception:
        log.debug("Не удалось привязать сервер модели к job-объекту", exc_info=True)


def llm_dir() -> Path:
    return paths.models_dir() / "llm"


def engine_dir() -> Path:
    return llm_dir() / "engine"


def default_binary() -> Path:
    """Путь к серверу llama.cpp.

    В собранном приложении движок лежит в ресурсах рядом с программой: он
    едет в дистрибутиве, чтобы заметки работали сразу после установки. В
    режиме разработки его там нет, и мы падаем обратно в каталог моделей,
    куда его кладут руками или докачкой.
    """
    name = "llama-server.exe" if os.name == "nt" else "llama-server"
    bundled = paths.resource_dir() / "engine" / name
    if bundled.exists():
        return bundled
    return engine_dir() / name


def find_model() -> Path | None:
    """Найти скачанную модель. Берём первый .gguf, какой лежит."""
    root = llm_dir()
    if not root.exists():
        return None
    files = sorted(root.rglob("*.gguf"))
    return files[0] if files else None

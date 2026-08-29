"""Скачивание весов модели с прогрессом и докачкой.

Интернет бывает слабым, поэтому:
- качаем каждый файл во временный `.part` и переименовываем только после
  успеха, чтобы битый огрызок никогда не выглядел как готовая модель;
- при обрыве продолжаем с того места, где встали (HTTP Range);
- сверяем размер с тем, что обещал сервер.

Берём напрямую по HTTPS, а не через huggingface_hub: так у нас есть
честный прогресс в процентах для UI и нет кэша с симлинками, который
потом мешает сборке бинарника.
"""

from __future__ import annotations

import logging
import threading
import urllib.request
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

HF_BASE = "https://huggingface.co/{repo}/resolve/main/{name}"
CHUNK = 1 << 16  # 64 КБ

# Колбэк прогресса: (имя файла, скачано байт, всего байт)
ProgressCallback = Callable[[str, int, int], None]


class DownloadCancelled(RuntimeError):
    """Пользователь остановил скачивание."""


class ModelDownloader:
    """Качает набор файлов модели в каталог. Можно отменить."""

    def __init__(self, repo: str, files: tuple[str, ...], dest: Path) -> None:
        self.repo = repo
        self.files = files
        self.dest = Path(dest)
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        self._cancel.set()

    def start(
        self,
        on_progress: ProgressCallback | None = None,
        on_done: Callable[[str | None], None] | None = None,
    ) -> None:
        """Запустить скачивание в фоне. on_done получает текст ошибки или None."""
        if self.is_running:
            log.warning("Скачивание уже идёт")
            return
        self._cancel.clear()

        def run() -> None:
            error: str | None = None
            try:
                self.run_blocking(on_progress)
            except DownloadCancelled:
                error = "Скачивание отменено"
            except Exception as exc:
                error = str(exc)
                log.exception("Скачивание модели не удалось")
            if on_done:
                on_done(error)

        self._thread = threading.Thread(target=run, name="model-download", daemon=True)
        self._thread.start()

    def run_blocking(self, on_progress: ProgressCallback | None = None) -> None:
        self.dest.mkdir(parents=True, exist_ok=True)
        for name in self.files:
            target = self.dest / name
            if target.exists():
                continue
            self._fetch(name, target, on_progress)

    def _fetch(self, name: str, target: Path, on_progress: ProgressCallback | None) -> None:
        part = target.with_suffix(target.suffix + ".part")
        done = part.stat().st_size if part.exists() else 0
        url = HF_BASE.format(repo=self.repo, name=name)

        request = urllib.request.Request(url, headers={"User-Agent": "konspekt"})
        if done:
            # Докачка: просим сервер отдать хвост.
            request.add_header("Range", f"bytes={done}-")
            log.info("Продолжаем качать %s с %d байт", name, done)

        with urllib.request.urlopen(request, timeout=60) as response:
            if done and response.status != 206:
                # Сервер не понял Range, начинаем файл заново.
                log.info("Докачка не поддержана, качаем %s целиком", name)
                done = 0
                part.unlink(missing_ok=True)
            total = int(response.headers.get("Content-Length") or 0) + done
            mode = "ab" if done else "wb"
            with open(part, mode) as fh:
                while True:
                    if self._cancel.is_set():
                        raise DownloadCancelled(name)
                    block = response.read(CHUNK)
                    if not block:
                        break
                    fh.write(block)
                    done += len(block)
                    if on_progress:
                        on_progress(name, done, total)

        if total and done < total:
            # Оборвалось молча: не выдаём огрызок за готовый файл.
            raise RuntimeError(f"Файл {name} скачан не полностью: {done} из {total}")
        part.replace(target)
        log.info("Файл %s готов (%d байт)", name, done)

    # --- сведения для UI -------------------------------------------------

    def downloaded_bytes(self) -> int:
        """Сколько уже лежит на диске, включая недокачанные куски."""
        total = 0
        for name in self.files:
            for path in (self.dest / name, self.dest / (name + ".part")):
                if path.exists():
                    total += path.stat().st_size
        return total

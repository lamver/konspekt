"""Расшифровать запись по ссылке: звук со страницы видео в очередь импорта.

Ссылку разбирает yt-dlp как библиотека, без отдельной программы и без
ffmpeg: берём только звуковую дорожку (или самый лёгкий файл со звуком,
если отдельной нет), а читает её тот же декодер, что и загруженные
файлы. Поэтому дальше всё как с файлом: очередь, прогресс, отмена,
расшифровка, заметки.

yt-dlp не обновляем сами и не качаем отдельно: программа, которая
скачивает и запускает код, для антивируса выглядит как троян. Сайты
меняются, и тогда разбор ссылки перестаёт работать до выхода нашей
новой версии; человеку говорим это прямо.

Сообщения об ошибках нарочно без технических подробностей и без
советов про обход блокировок: только «проверьте интернет» там, где сайт
не отвечает.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# Самая длинная запись по ссылке. Суточный стрим — не встреча, а часы
# фоновой работы и гигабайты: такое лучше отклонить сразу.
MAX_SECONDS = 6 * 3600

# Ссылка: http(s)://что-то.что-то/... Без схемы тоже принимаем
# («youtu.be/…», «rutube.ru/…»): так ссылки копируют из адресной строки.
_ССЫЛКА = re.compile(r"^(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:[/?#]\S*)?$", re.I)


class LinkError(RuntimeError):
    """Ссылку не удалось разобрать. code — ключ сообщения для человека."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class LinkCancelled(RuntimeError):
    """Человек отменил загрузку."""


@dataclass
class LinkAudio:
    path: Path
    title: str
    duration: float
    url: str


def normalize(text: str) -> str | None:
    """Ссылка из вставленного текста или None, если это не ссылка."""
    t = (text or "").strip().strip("<>\"'«»")
    # Пробелы внутри отсекает сама проверка ниже: \S* до конца строки.
    if not t or len(t) > 2000:
        return None
    if not _ССЫЛКА.match(t):
        return None
    if not re.match(r"^https?://", t, re.I):
        t = "https://" + t
    return t


def classify(error: BaseException) -> str:
    """Причина ошибки yt-dlp одним словом: по ней выбираем сообщение."""
    s = str(error).lower()
    if any(k in s for k in ("unsupported url", "no suitable extractor", "is not a valid url")):
        return "unsupported"
    if any(k in s for k in ("private video", "this video is private", "members-only", "join this channel")):
        return "private"
    if any(k in s for k in ("sign in", "log in", "login required", "cookies", "age-restricted",
                            "confirm your age", "confirm you’re not a bot", "confirm you're not a bot")):
        return "login"
    if any(k in s for k in ("video unavailable", "has been removed", "not available", "does not exist",
                            "http error 404", "no video formats", "no formats")):
        return "unavailable"
    if "is live" in s or "live event" in s or "premieres in" in s:
        return "live"
    if any(k in s for k in ("timed out", "timeout", "connection", "network", "getaddrinfo",
                            "name resolution", "ssl", "eof occurred", "forcibly closed",
                            "remote end closed", "http error 403", "http error 5", "unable to download")):
        return "network"
    return "failed"


def _опции(папка: Path, прогресс: Callable[[int, int], None], стоп: threading.Event) -> dict[str, Any]:
    def ход(d: dict[str, Any]) -> None:
        if стоп.is_set():
            # yt-dlp проверяет исключение из хука и прерывает загрузку.
            raise LinkCancelled()
        if d.get("status") == "downloading":
            всего = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            прогресс(int(d.get("downloaded_bytes") or 0), всего)

    class Журнал:
        def debug(self, m: str) -> None:
            pass

        def info(self, m: str) -> None:
            pass

        def warning(self, m: str) -> None:
            log.info("Ссылка: %s", m[:300])

        def error(self, m: str) -> None:
            log.warning("Ссылка: %s", m[:300])

    return {
        "logger": Журнал(),
        "noprogress": True,
        "noplaylist": True,
        # Только звук. Нет отдельной дорожки — самый лёгкий файл со
        # звуком: картинку декодер пропустит, а качать 4K ради звука
        # незачем.
        "format": "bestaudio/best[height<=480]/worst[acodec!=none]/worst",
        "outtmpl": str(папка / "запись.%(ext)s"),
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 10,
        "continuedl": True,
        "postprocessors": [],
        "progress_hooks": [ход],
        # Никаких чужих процессов. Программа, которая запускает другие
        # программы, для Defender похожа на троян: в 0.9.0 так уже было
        # (без_интерпретаторов_test.py). yt-dlp сам ищет ffmpeg и ffprobe
        # и, если они стоят у человека, качает ими трансляции по частям;
        # для YouTube спрашивает «deno --version». «prefer_ffmpeg» этого
        # не отключает. Отключает несуществующий путь к ffmpeg: тогда
        # yt-dlp считает, что ffmpeg нет, и даже не пробует запуск.
        "ffmpeg_location": str(папка / "без-ffmpeg"),
        "js_runtimes": {},
        "external_downloader": {"default": "native"},
        "fixup": "never",
        "check_formats": False,
    }


def _системный_прокси() -> bool:
    import urllib.request

    return any(k in ("http", "https", "all") for k in urllib.request.getproxies())


def fetch(url: str, папка: Path, прогресс: Callable[[int, int], None] | None = None,
          стоп: threading.Event | None = None) -> LinkAudio:
    """Скачать звук по ссылке в папку. Бросает LinkError или LinkCancelled.

    Сначала идём так же, как браузер: через прокси из настроек системы,
    если он есть. Не вышло по сети, а прокси был: пробуем ещё раз напрямую.
    Прокси бывает выключенным, медленным или не пускает к части сайтов, а
    человек увидел бы «проверьте подключение», хотя интернет у него есть.
    """
    стоп = стоп or threading.Event()
    прогресс = прогресс or (lambda done, total: None)
    try:
        return _скачать(url, папка, прогресс, стоп, {})
    except LinkError as беда:
        if беда.code != "network" or стоп.is_set() or not _системный_прокси():
            raise
    log.info("Ссылка %s: через прокси системы не вышло, пробуем напрямую", url)
    # Недокачанное через прокси не продолжаем: это мог быть другой формат.
    for f in папка.iterdir():
        if f.is_file():
            f.unlink(missing_ok=True)
    return _скачать(url, папка, прогресс, стоп, {"proxy": ""})


def _скачать(url: str, папка: Path, прогресс: Callable[[int, int], None],
             стоп: threading.Event, добавка: dict[str, Any]) -> LinkAudio:
    import yt_dlp
    from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor

    опции = {**_опции(папка, прогресс, стоп), **добавка}
    # Загрузчик трансляций спрашивает про ffmpeg мимо опций, через эту
    # переменную. Она своя у каждого потока, поэтому ставим её здесь.
    FFmpegPostProcessor._ffmpeg_location.set(опции["ffmpeg_location"])
    try:
        with yt_dlp.YoutubeDL(опции) as y:
            info = y.extract_info(url, download=False)
            if info.get("_type") == "playlist" or info.get("entries"):
                raise LinkError("playlist")
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                raise LinkError("live")
            длина = float(info.get("duration") or 0)
            if длина > MAX_SECONDS:
                raise LinkError("too_long")
            info = y.process_ie_result(info, download=True)
            путь = Path(y.prepare_filename(info))
    except (LinkError, LinkCancelled):
        raise
    except Exception as exc:
        if isinstance(getattr(exc, "exc_info", (None, None))[1], LinkCancelled) or стоп.is_set():
            raise LinkCancelled() from exc
        код = classify(exc)
        log.warning("Ссылка %s не разобрана (%s): %s", url, код, str(exc)[:300])
        raise LinkError(код, str(exc)) from exc

    if not путь.exists():
        # Имя файла могло поменяться при сохранении: берём единственный.
        файлы = [f for f in папка.iterdir() if f.is_file() and not f.name.endswith(".part")]
        if not файлы:
            raise LinkError("failed", "файл не сохранился")
        путь = файлы[0]
    название = str(info.get("title") or info.get("fulltitle") or "").strip()
    return LinkAudio(path=путь, title=название, duration=float(info.get("duration") or 0), url=url)


def temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="konspekt-link-"))


def cleanup(папка: Path | None) -> None:
    if папка is not None:
        shutil.rmtree(папка, ignore_errors=True)

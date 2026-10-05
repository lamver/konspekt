# -*- coding: utf-8 -*-
"""Расшифровать запись по ссылке: очередь, сервис, ошибки. Без сети.

Настоящий сайт в проверку не берём: он меняется, бывает недоступен, и
проверка падала бы не по нашей вине. Загрузку подменяем: она отдаёт
готовый звуковой файл, или ошибку, или ждёт отмены. Живую загрузку с
сайтов проверяет `tools/ссылки_вживую.py`.

Что сторожит:
1. Ссылка без схемы и в кавычках принимается, текст и адрес FTP — нет.
2. Звук по ссылке становится встречей: название из видео, ссылка в
   пометках, звук сохранён, встреча готова.
3. Ссылка, вставленная в папке, кладёт встречу в папку.
4. Ошибка сайта — понятный код, без встречи-пустышки и временных файлов.
5. Отмена во время загрузки останавливает её сразу, без встречи.
6. После пробного периода ссылка не принимается, и это сказано.
7. Ошибки yt-dlp раскладываются по понятным причинам, сеть — «network».
8. Прокси из настроек системы не дал скачать — вторая попытка напрямую,
   с чистой папкой. Без прокси, при отмене и при не сетевой ошибке
   второй попытки нет.
"""
from __future__ import annotations

import testenv  # noqa: F401  песочница вместо боевого профиля

import math
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

from app.core import link as link_mod

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


# --- 1. Что считается ссылкой ------------------------------------------------
случаи = {
    "https://www.youtube.com/watch?v=abc": "https://www.youtube.com/watch?v=abc",
    "youtu.be/abc": "https://youtu.be/abc",
    "  rutube.ru/video/1/  ": "https://rutube.ru/video/1/",
    "«https://vk.com/video-1_2»": "https://vk.com/video-1_2",
    "<https://vk.com/video1>": "https://vk.com/video1",
}
for ввод, надо in случаи.items():
    проверить(link_mod.normalize(ввод) == надо, f"{ввод!r} → {надо}")
for не in ["привет", "https://a b", "https://vk.com/video1 и ещё текст", "", "ftp://x.ru/a", "просто.текст с пробелом", "C:\\\\запись.mp3"]:
    проверить(link_mod.normalize(не) is None, f"{не!r} — не ссылка")

# --- 7. Причины ошибок --------------------------------------------------------
причины = {
    "ERROR: Unsupported URL: https://example.com": "unsupported",
    "ERROR: [youtube] x: Private video. Sign in if you've been granted access": "private",
    "ERROR: [youtube] x: Sign in to confirm you’re not a bot.": "login",
    "ERROR: [youtube] x: Video unavailable": "unavailable",
    "ERROR: unable to download video data: HTTP Error 403: Forbidden": "network",
    "ERROR: [rutube] x: _ssl.c:993: The handshake operation timed out": "network",
    "[WinError 10054] An existing connection was forcibly closed by the remote host": "network",
    "что-то совсем странное": "failed",
}
for текст, надо in причины.items():
    проверить(link_mod.classify(RuntimeError(текст)) == надо, f"«{текст[:45]}…» → {надо}")


# --- Сервис с подменённой загрузкой -------------------------------------------
def звуковой_файл(путь: Path, секунд: float = 3.0) -> None:
    t = np.arange(int(16000 * секунд), dtype=np.float32) / 16000
    данные = (np.sin(2 * math.pi * 440 * t) * 0.3 * 32000).astype(np.int16)
    with wave.open(str(путь), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(данные.tobytes())


тмп = Path(tempfile.mkdtemp())
from app.core import paths as paths_mod  # noqa: E402

(тмп / "audio").mkdir()
paths_mod.audio_dir = lambda: тмп / "audio"  # type: ignore[assignment]

from app.audio import NullCapture  # noqa: E402
from app.core.service import AppService  # noqa: E402
from app.storage.db import Store  # noqa: E402

папки_загрузки: list[Path] = []
режим = {"как": "ок"}
началось = threading.Event()


def подмена(url, папка, прогресс, стоп):
    папки_загрузки.append(папка)
    началось.set()
    if режим["как"] == "ошибка":
        raise link_mod.LinkError("network", "timed out")
    if режим["как"] == "ждать":
        for _ in range(200):
            if стоп.is_set():
                raise link_mod.LinkCancelled()
            time.sleep(0.02)
        raise link_mod.LinkError("failed", "не отменили")
    путь = папка / "запись.wav"
    звуковой_файл(путь)
    прогресс(50, 100)
    прогресс(100, 100)
    return link_mod.LinkAudio(path=путь, title="Лекция про встречи", duration=3.0, url=url)


с = AppService(store=Store(str(тмп / "t.db")), capture=NullCapture())
с.settings.asr.enabled = False
с._fetch_link = подмена  # type: ignore[assignment]
с.importer._fetch_link = подмена


def дождаться(task_id: str, сек: float = 30) -> dict:
    конец = time.time() + сек
    while time.time() < конец:
        for з in с.import_status()["tasks"]:
            if з["id"] == task_id and з["status"] in ("done", "failed", "cancelled"):
                return з
        time.sleep(0.05)
    return {}


try:
    # --- 2. Ссылка становится встречей -----------------------------------------
    проверить(not с.import_link("просто текст")["ok"], "текст вместо ссылки отклонён с объяснением")
    r = с.import_link("youtu.be/abc")
    проверить(r["ok"] and r["task"]["url"] == "https://youtu.be/abc", "ссылка встала в очередь")
    итог = дождаться(r["task"]["id"])
    проверить(итог.get("status") == "done", f"запись по ссылке разобрана: {итог.get('status')} {итог.get('error')}")
    встреча = с.store.get_meeting(итог.get("meeting_id") or "")
    проверить(встреча is not None and встреча.title == "Лекция про встречи", "название встречи — из видео")
    проверить(встреча is not None and "https://youtu.be/abc" in встреча.notes, "ссылка сохранена в пометках встречи")
    проверить(bool(с.store.list_audio_chunks(встреча.id)) if встреча else False, "звук сохранён рядом со встречей")
    проверить(итог.get("name") == "Лекция про встречи", "в очереди видно название, а не голую ссылку")
    проверить(all(not п.exists() for п in папки_загрузки), "временные файлы загрузки убраны")

    # --- 3. В папке ------------------------------------------------------------
    папка = с.create_folder("Лекции")
    r = с.import_link("https://rutube.ru/video/1/", папка["id"])
    итог = дождаться(r["task"]["id"])
    проверить(с.store.meeting_folders().get(итог.get("meeting_id")) == папка["id"],
              "ссылка, вставленная в папке, кладёт встречу в папку")

    # --- 4. Ошибка сайта -------------------------------------------------------
    было = len(с.store.list_meetings())
    режим["как"] = "ошибка"
    r = с.import_link("https://vk.com/video1")
    итог = дождаться(r["task"]["id"])
    проверить(итог.get("status") == "failed" and итог.get("error") == "network",
              f"ошибка сайта — понятная причина: {итог.get('error')}")
    проверить(len(с.store.list_meetings()) == было, "после ошибки нет встречи-пустышки")
    проверить(not папки_загрузки[-1].exists(), "после ошибки временная папка убрана")

    # --- 5. Отмена -------------------------------------------------------------
    режим["как"] = "ждать"
    началось.clear()
    r = с.import_link("https://vk.com/video2")
    началось.wait(5)
    время = time.time()
    с.cancel_import(r["task"]["id"])
    итог = дождаться(r["task"]["id"], 10)
    проверить(итог.get("status") == "cancelled" and time.time() - время < 2,
              f"отмена останавливает загрузку сразу: {итог.get('status')}, {time.time() - время:.1f} с")
    проверить(len(с.store.list_meetings()) == было, "после отмены нет встречи-пустышки")

    # --- 6. Пробный период -----------------------------------------------------
    с._trial_left = lambda: 0  # type: ignore[assignment]
    r = с.import_link("https://vk.com/video3")
    проверить(not r["ok"] and r.get("trial"), "после пробного периода ссылка не принимается")
finally:
    с.importer.stop()
    с.store.close()

# --- 8. Прокси системы и прямая попытка ---------------------------------------
_настоящее = (link_mod._скачать, link_mod._системный_прокси)
try:
    попытки: list[dict] = []
    ответы: list = []

    def подмена(url, папка, прогресс, стоп, добавка):
        попытки.append(dict(добавка))
        остаток = [f.name for f in папка.iterdir()]
        ответ = ответы.pop(0)
        if isinstance(ответ, Exception):
            (папка / "запись.webm.part").write_bytes(b"x")
            raise ответ
        return остаток

    link_mod._скачать = подмена  # type: ignore[assignment]

    def прогон(прокси: bool, *ответ, стоп=None):
        попытки.clear()
        ответы[:] = list(ответ)
        link_mod._системный_прокси = lambda: прокси  # type: ignore[assignment]
        папка = link_mod.temp_dir()
        try:
            return link_mod.fetch("https://vk.com/video1", папка, стоп=стоп)
        except Exception as exc:
            return exc
        finally:
            link_mod.cleanup(папка)

    итог = прогон(True, link_mod.LinkError("network"), "звук")
    проверить(len(попытки) == 2 and попытки[1] == {"proxy": ""},
              f"через прокси не вышло — вторая попытка напрямую: {попытки}")
    проверить(итог == [], f"прямая попытка начинается с чистой папки: {итог}")

    итог = прогон(False, link_mod.LinkError("network"), "звук")
    проверить(len(попытки) == 1 and isinstance(итог, link_mod.LinkError),
              "без прокси повторять незачем: одна попытка и ошибка сети")

    итог = прогон(True, link_mod.LinkError("private"), "звук")
    проверить(len(попытки) == 1 and getattr(итог, "code", "") == "private",
              "закрытое видео напрямую не перезапрашиваем")

    стоп = threading.Event()
    стоп.set()
    итог = прогон(True, link_mod.LinkError("network"), "звук", стоп=стоп)
    проверить(len(попытки) == 1, "после отмены второй попытки нет")

    итог = прогон(True, link_mod.LinkError("network"), link_mod.LinkError("network"))
    проверить(len(попытки) == 2 and getattr(итог, "code", "") == "network",
              "обе попытки не прошли — человеку одна ошибка сети")
finally:
    link_mod._скачать, link_mod._системный_прокси = _настоящее

# Прокси системы видим так же, как yt-dlp: по переменным окружения.
import os  # noqa: E402

_было = {k: os.environ.get(k) for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")}
try:
    for k in _было:
        os.environ.pop(k, None)
    os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
    проверить(link_mod._системный_прокси(), "прокси из окружения замечен")
finally:
    for k, v in _было.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

# Чужих процессов нет. На живой загрузке yt-dlp запускал ffmpeg, ffprobe
# и «deno --version», если находил их на компьютере: для антивируса это
# примета трояна. Здесь настоящий yt-dlp с нашими опциями, сеть не нужна.
import subprocess  # noqa: E402

import yt_dlp  # noqa: E402
from yt_dlp.downloader import get_suitable_downloader  # noqa: E402
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor  # noqa: E402

запуски: list = []
_popen = subprocess.Popen.__init__


def _сторож(self, args, *a, **k):
    запуски.append(args)
    raise OSError("запуск запрещён проверкой")


subprocess.Popen.__init__ = _сторож
try:
    with tempfile.TemporaryDirectory() as _п:
        опции = link_mod._опции(Path(_п), lambda *_: None, threading.Event())
        опции["logger"] = type("Тихо", (), {"debug": lambda *_: None, "info": lambda *_: None,
                                            "warning": lambda *_: None, "error": lambda *_: None})()
        with yt_dlp.YoutubeDL(опции) as y:
            ffmpeg_есть = FFmpegPostProcessor(y).available
            hls = get_suitable_downloader({"protocol": "m3u8_native", "url": "https://x/a.m3u8"},
                                          y.params).__name__
            js = list(y._js_runtimes) if hasattr(y, "_js_runtimes") else list(y.params["js_runtimes"])
        # Загрузчик трансляций спрашивает про ffmpeg без опций. Адрес
        # заведомо не адрес: yt-dlp откажет сразу, в сеть не пойдёт.
        try:
            link_mod._скачать("не-адрес", Path(_п), lambda *_: None, threading.Event(), {})
        except link_mod.LinkError:
            pass
        from yt_dlp.downloader.external import FFmpegFD
        ffmpeg_у_трансляций = FFmpegFD.available()
finally:
    subprocess.Popen.__init__ = _popen
проверить(not запуски, f"yt-dlp не запускает чужих программ: {запуски or 'ни одной'}")
проверить(not ffmpeg_есть, "ffmpeg для yt-dlp не существует, даже если стоит у человека")
проверить(not ffmpeg_у_трансляций, "загрузчик трансляций тоже не видит ffmpeg")
проверить(hls != "FFmpegFD", f"трансляции по частям качаются сами: {hls}")
проверить(not js, f"JS-движки не ищутся: {js or 'ни одного'}")

print()
print("Всё в порядке" if not БЕДЫ else f"Бед: {len(БЕДЫ)}")
raise SystemExit(1 if БЕДЫ else 0)

# -*- coding: utf-8 -*-
"""Веса докачиваются, даже если один источник отказал.

Hugging Face в России могут заблокировать, у кого-то его уже режет
провайдер. Тогда модели должны приехать с нашего CDN, а если отказал
CDN — с Hugging Face. Человек не должен ничего настраивать.

Поднимаем у себя два сервера: «CDN» и «Hugging Face», и ломаем их по
очереди.
"""
from __future__ import annotations

import testenv  # noqa: F401  песочница вместо боевого профиля

import http.server
import os
import tempfile
import threading
from pathlib import Path

import app.asr.download as загрузка

БЕДЫ: list[str] = []
СОДЕРЖИМОЕ = bytes(range(256)) * 1200


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


class Сервер(http.server.BaseHTTPRequestHandler):
    """Поведение задаётся на классе: отказ, блокировка, обрыв."""

    режим = "ок"
    запросы: list[tuple[str, int]] = []

    def log_message(self, *a):  # тишина в выводе
        pass

    def do_GET(self):
        начало = 0
        rng = self.headers.get("Range")
        if rng:
            начало = int(rng.split("=")[1].split("-")[0])
        type(self).запросы.append((self.path, начало))
        if self.режим == "404":
            self.send_error(404)
            return
        if self.режим == "451":
            self.send_error(451, "Unavailable For Legal Reasons")
            return
        хвост = СОДЕРЖИМОЕ[начало:]
        if self.режим == "обрыв":
            # Отдаёт первую треть и дальше молчит: соединение рвётся.
            хвост = хвост[: max(0, len(СОДЕРЖИМОЕ) // 3 - начало)]
        self.send_response(206 if начало else 200)
        self.send_header("Content-Length", str(len(СОДЕРЖИМОЕ) - начало))
        if начало:
            self.send_header("Content-Range", f"bytes {начало}-{len(СОДЕРЖИМОЕ) - 1}/{len(СОДЕРЖИМОЕ)}")
        self.end_headers()
        try:
            self.wfile.write(хвост)
        except OSError:
            pass


def поднять(имя: str) -> tuple[http.server.HTTPServer, type]:
    класс = type(имя, (Сервер,), {"режим": "ок", "запросы": []})
    с = http.server.ThreadingHTTPServer(("127.0.0.1", 0), класс)
    threading.Thread(target=с.serve_forever, daemon=True).start()
    return с, класс


cdn, CDN = поднять("CDN")
hf, HF = поднять("HF")
было = (загрузка.HF_BASE, загрузка.CDN_BASE, загрузка.RETRY_PAUSE, os.environ.get(загрузка.ENV_BASE))
загрузка.HF_BASE = f"http://127.0.0.1:{hf.server_address[1]}/hf/{{repo}}/{{name}}"
загрузка.CDN_BASE = f"http://127.0.0.1:{cdn.server_address[1]}/konspekt/models"
загрузка.RETRY_PAUSE = 0.01
os.environ.pop(загрузка.ENV_BASE, None)


def скачать() -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as врем:
        куда = Path(врем)
        try:
            загрузка.ModelDownloader("owner/repo", ("onnx/ves.bin",), куда).run_blocking()
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        файл = куда / "onnx" / "ves.bin"
        return файл.exists() and файл.read_bytes() == СОДЕРЖИМОЕ, ""


def сброс(cdn_режим: str, hf_режим: str) -> None:
    CDN.режим, HF.режим = cdn_режим, hf_режим
    CDN.запросы.clear()
    HF.запросы.clear()


try:
    # --- порядок: сначала свой CDN ---------------------------------------
    источники = загрузка.mirrors()
    проверить(len(источники) == 2 and "/konspekt/models/" in источники[0],
              f"первым идёт свой CDN, вторым Hugging Face: {источники}")

    сброс("ок", "ок")
    ок, _ = скачать()
    проверить(ок, "при живом CDN файл приезжает целым")
    проверить(CDN.запросы and CDN.запросы[0][0] == "/konspekt/models/owner/repo/onnx/ves.bin",
              f"путь на CDN повторяет Hugging Face: {CDN.запросы[:1]}")
    проверить(not HF.запросы, "при живом CDN в Hugging Face не ходим")

    # --- CDN не знает файла ----------------------------------------------
    сброс("404", "ок")
    ок, _ = скачать()
    проверить(ок and HF.запросы, "файла нет на CDN — приезжает с Hugging Face")
    проверить(len(CDN.запросы) == 1, f"на 404 CDN не долбим повторами: {len(CDN.запросы)} запросов")

    # --- Hugging Face заблокирован, CDN жив ------------------------------
    сброс("ок", "451")
    ок, _ = скачать()
    проверить(ок, "Hugging Face заблокирован — приезжает с CDN")

    # --- CDN рвётся посреди файла ----------------------------------------
    сброс("обрыв", "ок")
    ок, _ = скачать()
    проверить(ок, "CDN оборвался на трети — файл докачан с Hugging Face")
    проверить(HF.запросы and HF.запросы[0][1] > 0,
              f"Hugging Face продолжил с места обрыва, а не с нуля: {HF.запросы[:1]}")
    проверить(len(CDN.запросы) <= 5,
              f"застрявший CDN бросаем после нескольких пустых попыток, а не долбим до 200: {len(CDN.запросы)}")

    # --- отказали оба ----------------------------------------------------
    сброс("404", "451")
    ок, ошибка = скачать()
    проверить(not ок and "451" in ошибка, f"оба отказали — честная ошибка с причиной: {ошибка!r}")
    проверить(len(HF.запросы) == 1,
              f"на блокировку (451) не тратим время повторами: {len(HF.запросы)} запросов")

    # --- своя основа через переменную окружения --------------------------
    os.environ[загрузка.ENV_BASE] = "http://127.0.0.1:1/свой/"
    источники = загрузка.mirrors()
    проверить(len(источники) == 3 and источники[0] == "http://127.0.0.1:1/свой/{repo}/{name}",
              f"своя основа из переменной окружения идёт первой: {источники[0]}")
    os.environ.pop(загрузка.ENV_BASE)

    # --- CDN ещё не настроен ---------------------------------------------
    загрузка.CDN_BASE = ""
    проверить(загрузка.mirrors() == [загрузка.HF_BASE],
              "без CDN качаем с Hugging Face, как раньше")
finally:
    загрузка.HF_BASE, загрузка.CDN_BASE, загрузка.RETRY_PAUSE, env = было
    if env is not None:
        os.environ[загрузка.ENV_BASE] = env
    cdn.shutdown()
    hf.shutdown()

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Веса приезжают, даже если один источник отказал.")

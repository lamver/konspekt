"""Проба: позвонить модели и поговорить с ней голосом.

Запуск из корня репозитория:

    .venv/Scripts/python.exe tools/проба-голос/сервер.py [--умная] [--порт 8765]

Потом открыть в браузере http://127.0.0.1:8765 и нажать «Позвонить».
Лучше в Edge: там есть живые голоса «Microsoft ... Online (Natural)».

Как устроено, и почему так, если цель — скорость:

- Звук идёт из браузера сырыми кадрами PCM 16 кГц по WebSocket, без
  кодеков и без записи в файл. Поиск речи на сервере тот же, что в
  Конспекте (энергия сигнала), только хвост тишины короче: фраза
  считается законченной через 0,45 с паузы, а не через 0,6.
- Распознаёт GigaAM v3 (с пунктуацией), та же модель, что в Конспекте.
  На процессоре фраза в пару секунд разбирается за три-четыре десятых.
- Отвечает локальная Qwen3 через llama-server на видеокарте, с
  выключенным «размышлением» (иначе ответ начинается через десять
  секунд думания) и коротким окном контекста: 4 тысячи токенов хватает
  на разговор, а память видеокарты общая с продом.
- Ответ летит в браузер по токенам, браузер режет его на предложения и
  озвучивает первое, не дожидаясь остальных. Синтез речи — голоса самой
  системы через Web Speech API: ноль задержки на запуск и ничего
  качать. Ценой качества голоса, это проба.
- Пока модель говорит, микрофон не слушаем: иначе она слышит себя в
  колонках. Галочка «перебивать» снимает это ограничение (в наушниках).

Никаких новых зависимостей: WebSocket и HTTP на asyncio из стандартной
библиотеки, остальное из самого Конспекта.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import struct
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote

import numpy as np

КОРЕНЬ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(КОРЕНЬ))

from app.asr.gigaam import GigaamTranscriber  # noqa: E402
from app.asr.vad import SpeechSegmenter  # noqa: E402
from app.core import paths  # noqa: E402
from app.llm import local  # noqa: E402
from app.llm.client import LlmClient, LlmError  # noqa: E402

log = logging.getLogger("проба-голос")

ЗДЕСЬ = Path(__file__).resolve().parent
ЧАСТОТА = 16000

ПОДСКАЗКА = (
    "Ты собеседник в голосовом разговоре. Отвечай по-русски, коротко, "
    "одним-тремя предложениями, живым разговорным языком, как по телефону. "
    "Без списков, без markdown, без заголовков, без смайликов. "
    "Если вопрос непонятен, переспроси одной фразой."
)
ПАМЯТЬ_РЕПЛИК = 20  # сколько последних реплик помним
БЕЗ_РАЗМЫШЛЕНИЙ = {"enable_thinking": False}


# --- модель --------------------------------------------------------------


class Движок:
    """Распознавание и ответ. Один на процесс, общий для всех вкладок."""

    def __init__(self, уровень: str) -> None:
        self.asr = GigaamTranscriber(paths.models_dir() / "gigaam-v3-e2e-rnnt")
        модель = local.find_model(уровень)
        if модель is None:
            raise SystemExit(
                f"Модель уровня «{уровень}» не скачана: {local.tier_path(уровень)}"
            )
        # Окно контекста задано в модуле константой: уменьшаем перед
        # запуском, чтобы не отъедать у видеокарты память под 32k.
        local.CONTEXT_SIZE = 4096
        self.сервер = local.LocalServer(модель)
        self.клиент: LlmClient | None = None

    def прогреть(self) -> None:
        т = time.monotonic()
        self.asr.load()
        log.info("Распознавание загружено за %.1f с", time.monotonic() - т)
        т = time.monotonic()
        адрес = self.сервер.ensure_started()
        self.клиент = LlmClient(base_url=адрес, temperature=0.6)
        # Первый запрос прогревает веса на видеокарте, иначе первое
        # «алло» ждёт лишнюю секунду.
        self.клиент.complete(
            [{"role": "user", "content": "Привет"}],
            max_tokens=4,
            chat_template_kwargs=БЕЗ_РАЗМЫШЛЕНИЙ,
        )
        log.info("Модель готова за %.1f с: %s", time.monotonic() - т, адрес)

    def распознать(self, pcm: np.ndarray) -> str:
        куски = self.asr.transcribe(pcm, ЧАСТОТА)
        return " ".join(к.text for к in куски).strip()

    def ответить(self, история, на_кусок, стоп) -> str:
        assert self.клиент is not None
        return self.клиент.stream(
            [{"role": "system", "content": ПОДСКАЗКА}, *история],
            on_chunk=на_кусок,
            should_stop=стоп,
            max_tokens=300,
            chat_template_kwargs=БЕЗ_РАЗМЫШЛЕНИЙ,
        )

    def стоп(self) -> None:
        self.сервер.stop()


# --- WebSocket на стандартной библиотеке -----------------------------------

МАГИЯ = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class Закрыто(Exception):
    pass


async def прочитать_кадр(reader: asyncio.StreamReader):
    """Один кадр: (opcode, данные). Склеивает фрагменты."""
    данные = bytearray()
    код = None
    while True:
        шапка = await reader.readexactly(2)
        fin = шапка[0] & 0x80
        op = шапка[0] & 0x0F
        маска = шапка[1] & 0x80
        длина = шапка[1] & 0x7F
        if длина == 126:
            (длина,) = struct.unpack("!H", await reader.readexactly(2))
        elif длина == 127:
            (длина,) = struct.unpack("!Q", await reader.readexactly(8))
        ключ = await reader.readexactly(4) if маска else None
        тело = await reader.readexactly(длина)
        if ключ and длина:
            # Маску снимаем через numpy: байт за байтом на Python медленно,
            # а звук идёт десятками кадров в секунду.
            м = np.frombuffer((ключ * (длина // 4 + 1))[:длина], dtype=np.uint8)
            тело = np.bitwise_xor(np.frombuffer(тело, dtype=np.uint8), м).tobytes()
        if op == 0x8:
            raise Закрыто()
        if op in (0x9, 0xA):
            return op, тело
        if op != 0:
            код = op
        данные += тело
        if fin:
            return код, bytes(данные)


def кадр(op: int, данные: bytes) -> bytes:
    n = len(данные)
    if n < 126:
        шапка = bytes([0x80 | op, n])
    elif n < 65536:
        шапка = bytes([0x80 | op, 126]) + struct.pack("!H", n)
    else:
        шапка = bytes([0x80 | op, 127]) + struct.pack("!Q", n)
    return шапка + данные


class Соединение:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.lock = asyncio.Lock()

    async def слать(self, **сообщение) -> None:
        async with self.lock:
            self.writer.write(кадр(0x1, json.dumps(сообщение, ensure_ascii=False).encode()))
            await self.writer.drain()


# --- разговор --------------------------------------------------------------


class Разговор:
    """Одна вкладка: свой поиск речи, своя история, свой текущий ответ."""

    def __init__(self, движок: Движок, связь: Соединение) -> None:
        self.движок = движок
        self.связь = связь
        self.vad = SpeechSegmenter(silence_tail=0.45, min_speech=0.3, max_segment=12.0)
        self.история: list[dict] = []
        self.смещение = 0.0
        self.говорил = False
        self.стоп = threading.Event()
        self.задача: asyncio.Task | None = None
        self.цикл = asyncio.get_running_loop()

    async def звук(self, данные: bytes) -> None:
        данные = данные[: len(данные) & ~1]  # int16: нечётный хвост отбрасываем
        if not данные:
            return
        pcm = np.frombuffer(данные, dtype=np.int16).astype(np.float32) / 32768.0
        фразы = self.vad.feed(pcm, self.смещение)
        self.смещение += len(pcm) / ЧАСТОТА
        говорит = bool(self.vad._in_speech)
        if говорит and not self.говорил:
            # Человек заговорил: модель замолкает (браузер глушит озвучку),
            # недоговорённый ответ бросаем.
            await self.прервать()
            await self.связь.слать(type="vad", speaking=True)
        elif not говорит and self.говорил:
            await self.связь.слать(type="vad", speaking=False)
        self.говорил = говорит
        for фраза in фразы:
            await self.прервать()
            self.задача = asyncio.create_task(self.реплика(фраза.pcm))

    async def прервать(self) -> None:
        if self.задача and not self.задача.done():
            self.стоп.set()
            try:
                await self.задача
            except Exception:
                pass
        self.стоп = threading.Event()

    async def реплика(self, pcm: np.ndarray) -> None:
        т0 = time.monotonic()
        текст = await asyncio.to_thread(self.движок.распознать, pcm)
        asr_мс = int((time.monotonic() - т0) * 1000)
        if not текст:
            return
        await self.связь.слать(type="you", text=текст, ms=asr_мс)
        self.история.append({"role": "user", "content": текст})
        self.история[:] = self.история[-ПАМЯТЬ_РЕПЛИК:]
        стоп = self.стоп
        if стоп.is_set():
            # Человек продолжил говорить, пока мы распознавали: его слова
            # сохранены, а отвечать будем на всё сразу после следующей паузы.
            return

        очередь: asyncio.Queue = asyncio.Queue()

        def на_кусок(кусок: str) -> None:
            self.цикл.call_soon_threadsafe(очередь.put_nowait, кусок)

        def работа() -> str:
            try:
                return self.движок.ответить(list(self.история), на_кусок, стоп.is_set)
            finally:
                self.цикл.call_soon_threadsafe(очередь.put_nowait, None)

        задача = asyncio.ensure_future(asyncio.to_thread(работа))
        первый = None
        собрано: list[str] = []
        while True:
            кусок = await очередь.get()
            if кусок is None:
                break
            if первый is None:
                первый = int((time.monotonic() - т0) * 1000)
            собрано.append(кусок)
            await self.связь.слать(type="token", text=кусок)
        try:
            ответ = await задача
        except LlmError as exc:
            await self.связь.слать(type="error", text=str(exc))
            return
        ответ = (ответ or "".join(собрано)).strip()
        if ответ:
            self.история.append({"role": "assistant", "content": ответ})
        await self.связь.слать(
            type="done",
            text=ответ,
            interrupted=стоп.is_set(),
            ms={"asr": asr_мс, "first": первый, "total": int((time.monotonic() - т0) * 1000)},
        )


# --- HTTP ------------------------------------------------------------------


async def обслужить(движок: Движок, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        строка = await reader.readline()
        if not строка:
            return
        метод, путь, _ = строка.decode("latin-1").split(" ", 2)
        путь = unquote(путь.split("?", 1)[0])
        заголовки: dict[str, str] = {}
        while True:
            с = await reader.readline()
            if с in (b"\r\n", b"\n", b""):
                break
            имя, _, значение = с.decode("latin-1").partition(":")
            заголовки[имя.strip().lower()] = значение.strip()

        if путь == "/ws" and заголовки.get("upgrade", "").lower() == "websocket":
            ключ = заголовки["sec-websocket-key"]
            принято = base64.b64encode(hashlib.sha1(ключ.encode() + МАГИЯ).digest()).decode()
            writer.write(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {принято}\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            await разговор(движок, reader, writer)
            return

        if путь in ("/", "/index.html"):
            тело = (ЗДЕСЬ / "index.html").read_bytes()
            тип = "text/html; charset=utf-8"
            статус = "200 OK"
        elif путь == "/пример" and (пример := _пример_записи()) is not None:
            # Проверка цепочки без микрофона: страница прогоняет запись
            # через тот же путь, что и живой звук.
            тело = пример
            тип = "application/octet-stream"
            статус = "200 OK"
        else:
            тело = b"not found"
            тип = "text/plain"
            статус = "404 Not Found"
        writer.write(
            (
                f"HTTP/1.1 {статус}\r\nContent-Type: {тип}\r\n"
                f"Content-Length: {len(тело)}\r\nCache-Control: no-store\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
            + (b"" if метод == "HEAD" else тело)
        )
        await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, Закрыто):
        pass
    except Exception:
        log.exception("Ошибка соединения")
    finally:
        try:
            writer.close()
        except Exception:
            pass


def _пример_записи() -> bytes | None:
    """PCM 16 кГц из первой подходящей записи в audio_examples (папка
    не в репозитории, без неё кнопки «Пример» просто нет)."""
    import wave

    for путь in sorted((КОРЕНЬ / "audio_examples").glob("*.wav")):
        try:
            with wave.open(str(путь)) as w:
                if w.getframerate() != ЧАСТОТА or w.getnchannels() != 1 or w.getsampwidth() != 2:
                    continue
                секунд = w.getnframes() / ЧАСТОТА
                if not 2.0 <= секунд <= 8.0:
                    continue
                return w.readframes(w.getnframes())
        except Exception:
            continue
    return None


async def разговор(движок: Движок, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    связь = Соединение(writer)
    р = Разговор(движок, связь)
    await связь.слать(type="ready", model=движок.сервер.model_path.name)
    log.info("Звонок начался")
    try:
        while True:
            op, данные = await прочитать_кадр(reader)
            if op == 0x9:
                async with связь.lock:
                    writer.write(кадр(0xA, данные))
                    await writer.drain()
            elif op == 0x2:
                await р.звук(данные)
            elif op == 0x1:
                сообщение = json.loads(данные.decode())
                if сообщение.get("type") == "stop":
                    await р.прервать()
                elif сообщение.get("type") == "flush":
                    for фраза in р.vad.flush():
                        await р.прервать()
                        р.задача = asyncio.create_task(р.реплика(фраза.pcm))
                elif сообщение.get("type") == "clear":
                    await р.прервать()
                    р.история.clear()
    finally:
        await р.прервать()
        log.info("Звонок закончился")


async def главное(аргументы) -> None:
    движок = Движок("smart" if аргументы.умная else "fast")
    движок.прогреть()
    сервер = await asyncio.start_server(
        lambda r, w: обслужить(движок, r, w), "127.0.0.1", аргументы.порт
    )
    print(f"\nОткройте http://127.0.0.1:{аргументы.порт}  (Ctrl+C — выход)\n", flush=True)
    try:
        async with сервер:
            await сервер.serve_forever()
    finally:
        движок.стоп()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    п = argparse.ArgumentParser(description="Голосовой разговор с локальной моделью")
    п.add_argument("--порт", type=int, default=8765)
    п.add_argument("--умная", action="store_true", help="Qwen3 4B вместо 1.7B")
    try:
        asyncio.run(главное(п.parse_args()))
    except KeyboardInterrupt:
        pass

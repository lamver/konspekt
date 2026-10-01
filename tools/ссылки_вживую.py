"""Живая проверка «записи по ссылке»: настоящий yt-dlp и настоящие сайты.

    python tools/ссылки_вживую.py [ССЫЛКА ...]

Без аргументов берёт короткие ролики с VK Видео и Rutube: они работают
без обходных путей. Качает звук настоящей функцией программы, читает
его нашим декодером и печатает, что вышло. Базу и профиль не трогает.

В общие проверки не входит: сайты меняются и бывают недоступны, и
падение здесь не всегда означает ошибку в программе.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
КОРЕНЬ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(КОРЕНЬ))

from app.asr.audiofile import decode, probe  # noqa: E402
from app.core import link  # noqa: E402

ПО_УМОЛЧАНИЮ = [
    "https://vk.com/video-22822305_456239018",
    "https://rutube.ru/video/c6cc4d620b1d4338901770a44b3e82f4/",
]


def main() -> int:
    ссылки = sys.argv[1:] or ПО_УМОЛЧАНИЮ
    бед = 0
    for url in ссылки:
        папка = link.temp_dir()
        t = time.time()
        шаги = []
        try:
            звук = link.fetch(url, папка, lambda d, всего: шаги.append(d))
            i = probe(звук.path)
            секунд = sum(len(pcm) for _, pcm, _ in decode(звук.path, chunk_seconds=30.0)) / 16000
            print(f"[ок] {url}\n     «{звук.title}», {звук.path.suffix}, {звук.path.stat().st_size // 1024} КБ, "
                  f"длина {i.duration:.0f} с, прочитано {секунд:.0f} с, за {time.time() - t:.0f} с, "
                  f"прогресс приходил {len(шаги)} раз")
            if секунд < min(10, i.duration * 0.9):
                print("     [БЕДА] звука прочитано меньше длины записи")
                бед += 1
        except link.LinkError as e:
            print(f"[НЕ ВЫШЛО] {url}: причина «{e.code}» ({e.detail[:160]})")
            бед += 1
        finally:
            link.cleanup(папка)
    return 1 if бед else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Замер распознавания по языкам на открытом наборе Google FLEURS.

Зачем. «Whisper знает немецкий» — это обещание из чужой статьи. Прежде
чем писать на сайте «распознаёт немецкий», меряем на своём конвейере:
тот же определитель языка, тот же роутер, те же модели, что у человека.

Набор. FLEURS (google/fleurs, CC BY 4.0): носители читают предложения из
Википедии, у каждой записи есть эталонный текст с пунктуацией. Это
чтение, а не живой созвон, поэтому цифры — нижняя граница ошибок: на
шумном созвоне с перебиваниями будет хуже.

Весь архив языка (~230 МБ) не качаем: читаем его потоком и обрываем,
как только набрали нужное число фраз.

    python tools/замер_распознавания.py                 # de sr es en ru, по 40 фраз
    python tools/замер_распознавания.py de sr --фраз 80

Что считаем по каждому языку:
- WER — доля ошибок в словах после приведения к нижнему регистру и
  удаления знаков (так считают все, иначе сравнивать не с чем);
- язык угадан — сколько фраз определитель отправил туда, куда надо;
- WER у носителя — язык по умолчанию тот же, что в записи: так программа
  настроится сама у немца или серба (язык берётся из Windows), а обычный
  WER — у русского пользователя, к которому на встречу пришёл иностранец;
- WER с подсказкой языка — та же запись, но язык назван явно: так видно,
  сколько ошибок от модели, а сколько от определителя;
- пунктуация — есть ли в ответе знаки препинания вообще;
- скорость — секунды распознавания на секунду звука (меньше 1 — быстрее
  реального времени).
"""

from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import sys
import tarfile
import time
import unicodedata
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import testenv  # noqa: E402,F401  русский вывод в консоли Windows

КОРЕНЬ = Path(__file__).resolve().parent.parent
КЭШ = КОРЕНЬ / "models" / "_замер"   # models/ не в git: записи чужие
ОСНОВА = "https://huggingface.co/datasets/google/fleurs/resolve/main/data/{код}/{файл}"
КОДЫ = {"de": "de_de", "sr": "sr_rs", "es": "es_419", "en": "en_us", "ru": "ru_ru",
        "fr": "fr_fr", "it": "it_it", "pt": "pt_br", "pl": "pl_pl", "uk": "uk_ua"}


def скачать_фразы(язык: str, сколько: int) -> list[tuple[Path, str]]:
    """Первые `сколько` фраз из dev-части: файл звука и эталон с пунктуацией."""
    код = КОДЫ[язык]
    папка = КЭШ / код
    папка.mkdir(parents=True, exist_ok=True)
    tsv = папка / "dev.tsv"
    if not tsv.exists():
        tsv.write_bytes(urllib.request.urlopen(ОСНОВА.format(код=код, файл="dev.tsv"), timeout=60).read())
    эталоны: dict[str, str] = {}
    with tsv.open(encoding="utf-8") as f:
        for строка in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(строка) >= 3:
                эталоны.setdefault(строка[1], строка[2])

    есть = [п for п in папка.glob("*.wav") if п.name in эталоны]
    if len(есть) < сколько:
        print(f"  качаем {код}: читаем архив потоком до {сколько} фраз")
        ответ = urllib.request.urlopen(ОСНОВА.format(код=код, файл="audio/dev.tar.gz"), timeout=60)
        with tarfile.open(fileobj=ответ, mode="r|gz") as архив:
            for член in архив:
                имя = Path(член.name).name
                if not член.isfile() or имя not in эталоны:
                    continue
                путь = папка / имя
                if not путь.exists():
                    путь.write_bytes(архив.extractfile(член).read())
                есть.append(путь)
                if len({п.name for п in есть}) >= сколько:
                    break
        ответ.close()
    выбор = sorted({п.name: п for п in есть}.values(), key=lambda п: п.name)[:сколько]
    return [(п, эталоны[п.name]) for п in выбор]


def читать(путь: Path) -> np.ndarray:
    """Звук FLEURS — WAV с плавающей точкой: его разбирает ffmpeg, не модуль wave."""
    сырое = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(путь), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(сырое, dtype="<f4").astype(np.float32)


def слова(текст: str, язык: str) -> list[str]:
    """Приведение к виду, в котором сравнивают все: регистр и знаки прочь."""
    from app.asr.whisper import латиница

    if язык == "sr":
        текст = латиница(текст)  # эталон FLEURS кириллицей, мы пишем латиницей
    текст = unicodedata.normalize("NFC", текст.lower()).replace("ё", "е")
    return re.findall(r"[\w]+", текст)


def ошибки(эталон: list[str], ответ: list[str]) -> int:
    """Расстояние Левенштейна по словам."""
    прошлая = list(range(len(ответ) + 1))
    for i, э in enumerate(эталон, 1):
        текущая = [i] + [0] * len(ответ)
        for j, о in enumerate(ответ, 1):
            текущая[j] = min(прошлая[j] + 1, текущая[j - 1] + 1, прошлая[j - 1] + (э != о))
        прошлая = текущая
    return прошлая[-1]


def main() -> int:
    аргументы = [a for a in sys.argv[1:] if not a.startswith("--")]
    языки = аргументы or ["de", "sr", "es", "en", "ru"]
    сколько = 40
    if "--фраз" in sys.argv:
        сколько = int(sys.argv[sys.argv.index("--фраз") + 1])
        языки = [я for я in языки if я != str(сколько)]

    from app.asr.gigaam import MODEL_DIR_NAME as GIGAAM_DIR, GigaamTranscriber
    from app.asr.langid import MODEL_DIR_NAME as LANGID_DIR, LanguageDetector
    from app.asr.router import LanguageRouter
    from app.asr.whisper import SIZES, WhisperTranscriber
    from app.core import paths

    модели = paths.models_dir()
    whisper = WhisperTranscriber(модели / SIZES["small"]["dir"])
    гигаам = GigaamTranscriber(модели / GIGAAM_DIR)
    определитель = LanguageDetector(модели / LANGID_DIR)
    роутер = LanguageRouter(гигаам, определитель, whisper)

    итог = {}
    for язык in языки:
        print(f"[{язык}]")
        фразы = скачать_фразы(язык, сколько)
        ош = ош_подсказка = ош_носитель = всего = угадан = со_знаками = 0
        носитель = LanguageRouter(гигаам, определитель, whisper, fallback_lang=язык)
        звук_сек = думал_сек = 0.0
        примеры = []
        куда: dict[str, int] = {}
        for путь, эталон in фразы:
            x = читать(путь)
            роутер.reset()
            t0 = time.perf_counter()
            куски = list(роутер.transcribe(x, 16000, "замер", 0.0, "them"))
            думал_сек += time.perf_counter() - t0
            звук_сек += x.size / 16000
            ответ = " ".join(к.text for к in куски)
            язык_ответа = куски[0].lang if куски else ""
            куда[язык_ответа or "—"] = куда.get(язык_ответа or "—", 0) + 1
            угадан += язык_ответа == язык or (язык == "ru" and язык_ответа in ("ru", ""))
            со_знаками += bool(re.search(r"[.,!?¿¡;:]", ответ))
            э = слова(эталон, язык)
            ош += ошибки(э, слова(ответ, язык))
            всего += len(э)
            if язык != "ru":
                носитель.reset()
                свои = list(носитель.transcribe(x, 16000, "замер", 0.0, "them"))
                ош_носитель += ошибки(э, слова(" ".join(к.text for к in свои), язык))
                подсказка = list(whisper.transcribe(x, 16000, "замер", 0.0, "them", lang=язык))
                ош_подсказка += ошибки(э, слова(" ".join(к.text for к in подсказка), язык))
            if len(примеры) < 3:
                примеры.append((эталон, ответ, язык_ответа))
        n = len(фразы)
        итог[язык] = {
            "фраз": n,
            "WER": round(100 * ош / max(всего, 1), 1),
            "WER_у_носителя": None if язык == "ru" else round(100 * ош_носитель / max(всего, 1), 1),
            "WER_с_подсказкой": None if язык == "ru" else round(100 * ош_подсказка / max(всего, 1), 1),
            "язык_угадан": f"{угадан}/{n}",
            "куда_ушли": куда,
            "пунктуация": f"{со_знаками}/{n}",
            "скорость": round(думал_сек / max(звук_сек, 1e-9), 2),
        }
        print(f"  {итог[язык]}")
        for э, о, л in примеры:
            print(f"    эталон: {э}\n    ответ:  {о}  [{л}]")
    print()
    print(json.dumps(итог, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

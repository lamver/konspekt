"""Замер русского: GigaAM против Whisper small и large-v3-turbo.

Зачем. На сайте и в статьях (просьба Seo-marketing) хочется сказать не
«русский через GigaAM, потому что так решили», а «потому что он ошибается
в N раз реже». Для этого три модели прогоняются по одним и тем же
записям, и считается доля ошибок в словах.

Два набора:
- FLEURS ru_ru (google/fleurs, CC BY 4.0): носители читают предложения
  из Википедии. Чистое чтение, нижняя граница ошибок. Записи берутся из
  кэша `tools/замер_распознавания.py`, при нужде докачиваются им же.
- Живые телефонные обрывки из `audio_examples/*.wav` с эталоном в
  одноимённом `.txt`: короткие, с шумом и междометиями. Их мало, цифра
  по ним грубая, но это то, с чем программа встречается на деле.

Whisper turbo в сборке нет, его выгрузка лежит в
`models/_замер/модели/whisper-turbo` (та же раскладка, что у
onnx-community, int8). Если папки нет, turbo пропускается.

    python tools/замер_русского.py            # 40 фраз FLEURS + обрывки
    python tools/замер_русского.py --фраз 80
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import testenv  # noqa: E402,F401  русский вывод в консоли Windows

from замер_распознавания import КЭШ, ошибки, скачать_фразы, слова, читать  # noqa: E402

КОРЕНЬ = Path(__file__).resolve().parent.parent
ОБРЫВКИ = КОРЕНЬ / "audio_examples"
ТУРБО = КЭШ / "модели" / "whisper-turbo"


def живые_обрывки() -> list[tuple[Path, str]]:
    итог = []
    for txt in sorted(ОБРЫВКИ.glob("*.txt")):
        wav = txt.with_suffix(".wav")
        if not wav.exists():
            continue
        try:
            эталон = txt.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError:
            continue  # не текст, а переименованный mp3
        if эталон and any("а" <= с <= "я" for с in эталон.lower()):
            итог.append((wav, эталон))
    return итог


def прогнать(имя: str, распознать, записи: list[tuple[Path, str]]) -> dict:
    ош = всего = 0
    звук = думал = 0.0
    примеры = []
    for путь, эталон in записи:
        x = читать(путь)
        t0 = time.perf_counter()
        ответ = распознать(x)
        думал += time.perf_counter() - t0
        звук += x.size / 16000
        э = слова(эталон, "ru")
        ош += ошибки(э, слова(ответ, "ru"))
        всего += len(э)
        if len(примеры) < 3:
            примеры.append((эталон, ответ))
    итог = {
        "записей": len(записи),
        "WER": round(100 * ош / max(всего, 1), 1),
        "скорость": round(думал / max(звук, 1e-9), 2),
    }
    print(f"  {имя}: {итог}")
    for э, о in примеры:
        print(f"    эталон: {э}\n    ответ:  {о}")
    return итог


def main() -> int:
    сколько = 40
    if "--фраз" in sys.argv:
        сколько = int(sys.argv[sys.argv.index("--фраз") + 1])

    from app.asr.gigaam import MODEL_DIR_NAME as GIGAAM_DIR, GigaamTranscriber
    from app.asr.whisper import SIZES, WhisperTranscriber
    from app.core import paths

    модели = paths.models_dir()
    гигаам = GigaamTranscriber(модели / GIGAAM_DIR)
    small = WhisperTranscriber(модели / SIZES["small"]["dir"])
    участники = {
        "GigaAM": lambda x: " ".join(к.text for к in гигаам.transcribe(x, 16000, "замер", 0.0, "them")),
        "Whisper small": lambda x: " ".join(
            к.text for к in small.transcribe(x, 16000, "замер", 0.0, "them", lang="ru")),
    }
    if ТУРБО.exists():
        turbo = WhisperTranscriber(ТУРБО)
        участники["Whisper large-v3-turbo"] = lambda x: " ".join(
            к.text for к in turbo.transcribe(x, 16000, "замер", 0.0, "them", lang="ru"))
    else:
        print(f"Нет {ТУРБО}, turbo пропущен")

    наборы = {
        "FLEURS, чтение": скачать_фразы("ru", сколько),
        "телефонные обрывки": живые_обрывки(),
    }
    итог: dict[str, dict[str, dict]] = {}
    for название, записи in наборы.items():
        print(f"[{название}: {len(записи)} записей]")
        if not записи:
            continue
        итог[название] = {имя: прогнать(имя, ф, записи) for имя, ф in участники.items()}
    print()
    print(json.dumps(итог, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

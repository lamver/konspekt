"""Собрать обзорный ролик на нужном языке: тайминги, звук, кадры, mp4.

    python promo/обзор/собрать.py en              — всё целиком
    python promo/обзор/собрать.py en --без-видео  — только тайминги и звук
                                                    (чтобы смотреть ролик.html вживую)

Что делает:
1. Меряет реплики озвучки promo/голос/<язык>/vo-NN.wav (для английского
   годится и promo/голос/vo-NN.wav) и пишет их длины в
   сборка/<язык>/голос.js. По ним ролик.html растягивает сцены.
   Реплики нет — длина сцены прикидывается по числу слов.
2. Сводит звук: озвучку и мягкие ASMR-акценты из promo/звук.py. Под
   голосом акценты приглушаются, чтобы не спорить с речью.
3. Снимает кадры через promo/снять.mjs и склеивает mp4 через ffmpeg.

Результат: promo/out/konspekt-обзор-<язык>.mp4.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

ПАПКА = Path(__file__).resolve().parent
PROMO = ПАПКА.parent
ЧАСТОТА = 48_000
КАДРОВ_В_СЕКУНДУ = 30


def json_из(путь: Path) -> dict:
    текст = путь.read_text(encoding="utf-8")
    м = re.search(r"/\*JSON\*/(.*?)/\*/JSON\*/", текст, re.S)
    if not м:
        raise SystemExit(f"в {путь} нет блока JSON")
    return json.loads(м.group(1))


def звуки_первого_ролика():
    """Синтез ASMR-звуков берём у первого ролика: один набор на все."""
    spec = importlib.util.spec_from_file_location("звук_промо", PROMO / "звук.py")
    модуль = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(модуль)
    return модуль


def прочитать_звук(путь: Path) -> np.ndarray:
    """Любой файл озвучки в стерео 48 кГц float. Формат разбирает ffmpeg."""
    сырое = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(путь), "-f", "f32le", "-ac", "2", "-ar", str(ЧАСТОТА), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(сырое, dtype="<f4").reshape(-1, 2).astype(np.float64)


def обрезать_тишину(x: np.ndarray, порог: float = 0.008) -> np.ndarray:
    """Синтез любит тишину по краям: она съедает ритм сцен."""
    громко = np.where(np.max(np.abs(x), axis=1) > порог)[0]
    if громко.size == 0:
        return x
    запас = int(0.03 * ЧАСТОТА)
    return x[max(0, громко[0] - запас): громко[-1] + запас]


def найти_голос(язык: str, номер: int) -> Path | None:
    for папка in (PROMO / "голос" / язык, PROMO / "голос") if язык == "en" else (PROMO / "голос" / язык,):
        for расширение in ("wav", "mp3", "flac", "ogg", "m4a"):
            путь = папка / f"vo-{номер:02d}.{расширение}"
            if путь.exists():
                return путь
    return None


def разметить(сц: dict, тексты: dict, голос: dict[int, float]) -> list[dict]:
    """Тот же расчёт, что в ролик.html: реплика + вход + хвост, не короче «мин»."""
    t = 0.0
    out = []
    for с in сц["сцены"]:
        реплика = тексты["сцены"].get(с["id"], {}).get("голос", "")
        прикидка = len(реплика.split()) / тексты.get("слов_в_секунду", 2.6)
        длина_голоса = голос.get(с["голос"], прикидка)
        длина = max(с["мин"], сц["голос_вход"] + длина_голоса + сц["голос_хвост"])
        out.append({**с, "начало": t, "конец": t + длина})
        t += длина
    return out


def main() -> int:
    аргументы = [a for a in sys.argv[1:] if not a.startswith("--")]
    язык = аргументы[0] if аргументы else "en"
    без_видео = "--без-видео" in sys.argv

    сц = json_из(ПАПКА / "сценарий.js")
    тексты = json_из(ПАПКА / "тексты" / f"{язык}.js")

    # 1. Озвучка: длины реплик.
    реплики: dict[int, np.ndarray] = {}
    for с in сц["сцены"]:
        номер = с.get("голос")
        if номер is None:
            continue
        путь = найти_голос(язык, номер)
        if путь:
            реплики[номер] = обрезать_тишину(прочитать_звук(путь))
    голос = {н: len(x) / ЧАСТОТА for н, x in реплики.items()}
    нет = [с["голос"] for с in сц["сцены"] if с.get("голос") is not None and с["голос"] not in голос]
    print(f"озвучка: {len(голос)} реплик" + (f", нет {нет} — длина по прикидке" if нет else ""))

    сборка = ПАПКА / "сборка" / язык
    сборка.mkdir(parents=True, exist_ok=True)
    (сборка / "голос.js").write_text(
        "/* Длины реплик озвучки в секундах. Пишет собрать.py, руками не править. */\n"
        f"window.ГОЛОС = {json.dumps({str(н): round(д, 3) for н, д in голос.items()})};\n",
        encoding="utf-8",
    )

    сцены = разметить(сц, тексты, голос)
    длина = сцены[-1]["конец"]
    for с in сцены:
        print(f"  {с['id']:<11} {с['начало']:6.2f} – {с['конец']:6.2f}")
    print(f"длина ролика: {длина:.1f} с")

    # 2. Звук.
    з = звуки_первого_ролика()
    n = int(длина * ЧАСТОТА)
    акценты = np.zeros((n + ЧАСТОТА * 3, 2))
    речь = np.zeros_like(акценты)
    for с in сцены:
        д = с["конец"] - с["начало"]
        for событие in с.get("звуки", []):
            л, п = з.ВИДЫ[событие["вид"]](событие)
            i = int((с["начало"] + событие["доля"] * д) * ЧАСТОТА)
            г = событие.get("г", 0.7)
            акценты[i:i + len(л), 0] += л * г
            акценты[i:i + len(п), 1] += п * г
        x = реплики.get(с.get("голос"))
        if x is not None:
            i = int((с["начало"] + сц["голос_вход"]) * ЧАСТОТА)
            x = x / (np.max(np.abs(x)) or 1) * 0.9
            речь[i:i + len(x)] += x

    # Акценты под голосом тише: огибающая речи, сглаженная на 0.3 с.
    if реплики:
        уровень = np.max(np.abs(речь), axis=1)
        окно = int(0.3 * ЧАСТОТА)
        гладкий = np.convolve(уровень, np.ones(окно) / окно, mode="same")
        приглушение = 1 - 0.6 * np.clip(гладкий / 0.05, 0, 1)
        акценты *= приглушение[:, None]
        акценты *= 0.55   # с голосом акценты — фон, а не главное
    акценты[:, 0] = з.отзвук(акценты[:, 0])
    акценты[:, 1] = з.отзвук(акценты[:, 1])
    воздух = np.zeros_like(акценты)
    for к in range(2):
        полоса = з.полоса(з.шум(len(акценты) / ЧАСТОТА + 0.01), 150, 3000)
        воздух[:, к] = полоса[:len(акценты)]
    смесь = (акценты + речь + воздух / np.max(np.abs(воздух)) * 0.005)[:n]

    края = np.ones(n)
    края[: int(0.02 * ЧАСТОТА)] = np.linspace(0, 1, int(0.02 * ЧАСТОТА))
    края[-int(0.8 * ЧАСТОТА):] = np.linspace(1, 0, int(0.8 * ЧАСТОТА)) ** 2
    смесь *= края[:, None]
    смесь *= 10 ** (-1.5 / 20) / (np.max(np.abs(смесь)) or 1)

    вывод = PROMO / "out" / f"обзор-{язык}"
    вывод.mkdir(parents=True, exist_ok=True)
    звук = вывод / "звук.wav"
    with wave.open(str(звук), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(ЧАСТОТА)
        w.writeframes((np.clip(смесь, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"звук: {звук}")

    if без_видео:
        print(f"смотреть вживую: promo/обзор/ролик.html?язык={язык}")
        return 0

    # 3. Кадры и mp4.
    кадры = вывод / "кадры"
    node = shutil.which("node") or "node"
    subprocess.run([node, str(PROMO / "снять.mjs"), str(КАДРОВ_В_СЕКУНДУ), "обзор/ролик.html",
                    f"язык={язык}", str(кадры.relative_to(PROMO))], check=True)
    ролик = PROMO / "out" / f"konspekt-обзор-{язык}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-framerate", str(КАДРОВ_В_СЕКУНДУ), "-i", str(кадры / "%05d.png"),
        "-i", str(звук), "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(ролик),
    ], check=True)
    print(f"готово: {ролик}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Список весов моделей Konspekt и их копия для своего CDN.

    python tools/зеркало_моделей.py список           — что и откуда качаем
    python tools/зеркало_моделей.py скачать ПАПКА    — скачать всё в раскладке CDN
    python tools/зеркало_моделей.py сверить ОСНОВА   — проверить, что CDN отдаёт те же файлы

Раскладка на CDN повторяет Hugging Face: <основа>/<владелец>/<репозиторий>/<файл>.
Загрузчик программы подставляет свою основу к тем же репозиторию и
файлу, поэтому отдельной таблицы путей не нужно.

Ссылки закреплены на ревизию, а файлы сверяются по sha256: если автор
перезальёт модель на Hugging Face, на CDN не уедет другой файл под
старым именем.

Архивы не делаем. Замер 30.09 на тех же весах: GGUF жмётся на 2–4%
(это уже сжатые квантованные числа), и это 9 из 10 ГБ; ONNX в int8
жмётся на 27–35%, но их всего 0.5 ГБ. Архив ломает докачку по Range
на слабом канале и требует шага распаковки, где тоже можно упасть.
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Имя, репозиторий, ревизия, файлы: (путь, размер, sha256 или None для
# мелких файлов без LFS — у них сверяем только размер).
МОДЕЛИ: list[tuple[str, str, str, list[tuple[str, int, str | None]]]] = []


def _загрузить_список() -> None:
    путь = Path(__file__).with_name("модели.json")
    for m in json.loads(путь.read_text(encoding="utf-8")):
        МОДЕЛИ.append((m["name"], m["repo"], m["revision"],
                       [(f["file"], f["size"], f["sha256"]) for f in m["files"]]))


def hf_url(repo: str, rev: str, файл: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/{rev}/{файл}"


def sha256(путь: Path) -> str:
    h = hashlib.sha256()
    with open(путь, "rb") as fh:
        for блок in iter(lambda: fh.read(1 << 20), b""):
            h.update(блок)
    return h.hexdigest()


def скачать_файл(url: str, цель: Path, размер: int) -> None:
    """С докачкой: оборванный кусок продолжается с места обрыва."""
    цель.parent.mkdir(parents=True, exist_ok=True)
    кусок = цель.with_name(цель.name + ".part")
    for _ in range(50):
        есть = кусок.stat().st_size if кусок.exists() else 0
        if есть >= размер:
            break
        req = urllib.request.Request(url, headers={"User-Agent": "konspekt-mirror"})
        if есть:
            req.add_header("Range", f"bytes={есть}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(кусок, "ab" if есть else "wb") as fh:
                if есть and r.status != 206:
                    fh.truncate(0)
                while блок := r.read(1 << 20):
                    fh.write(блок)
        except OSError as e:
            print(f"   обрыв на {есть} байт: {e}, продолжаем")
    кусок.replace(цель)


def скачать(папка: Path) -> int:
    манифест = []
    бед = 0
    for имя, repo, rev, файлы in МОДЕЛИ:
        print(f"== {имя}")
        for файл, размер, sha in файлы:
            цель = папка / repo / файл
            if not (цель.exists() and цель.stat().st_size == размер):
                print(f"   качаем {файл} ({размер / 1e6:.1f} МБ)")
                скачать_файл(hf_url(repo, rev, файл), цель, размер)
            ок = цель.stat().st_size == размер and (sha is None or sha256(цель) == sha)
            print(f"   {'[ок]' if ок else '[БЕДА]'} {файл}")
            бед += not ок
            манифест.append({"path": f"{repo}/{файл}", "size": размер, "sha256": sha})
    (папка / "manifest.json").write_text(json.dumps(манифест, indent=2), encoding="utf-8")
    print(f"\nГотово, бед: {бед}. Выложите содержимое {папка} на CDN как есть.")
    return 1 if бед else 0


def сверить(основа: str) -> int:
    """Спросить у CDN размер каждого файла и поддержку Range."""
    бед = 0
    for имя, repo, rev, файлы in МОДЕЛИ:
        for файл, размер, _ in файлы:
            url = f"{основа.rstrip('/')}/{repo}/{файл}"
            req = urllib.request.Request(url, headers={"User-Agent": "konspekt-mirror", "Range": "bytes=0-0"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    всего = int((r.headers.get("Content-Range") or "/0").rsplit("/", 1)[1])
                    ок = r.status == 206 and всего == размер
                    причина = "" if ок else f"статус {r.status}, размер {всего} вместо {размер}"
            except OSError as e:
                ок, причина = False, str(e)
            print(f"{'[ок]' if ок else '[БЕДА]'} {repo}/{файл} {причина}")
            бед += not ок
    return 1 if бед else 0


def main() -> int:
    _загрузить_список()
    if len(sys.argv) >= 2 and sys.argv[1] == "список":
        for имя, repo, rev, файлы in МОДЕЛИ:
            print(f"{имя}")
            for файл, размер, _ in файлы:
                print(f"  {размер / 1e6:9.1f} МБ  {hf_url(repo, rev, файл)}")
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == "скачать":
        return скачать(Path(sys.argv[2]))
    if len(sys.argv) >= 3 and sys.argv[1] == "сверить":
        return сверить(sys.argv[2])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

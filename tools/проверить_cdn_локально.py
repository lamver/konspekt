"""Проверить загрузку моделей со своего CDN у себя на компьютере.

    python tools/проверить_cdn_локально.py            — поднять «CDN» из уже
        скачанных моделей и скачать их через загрузчик программы, как будто
        Hugging Face заблокирован
    python tools/проверить_cdn_локально.py АДРЕС      — то же, но с настоящего
        CDN (например https://models.aisearch.ru/konspekt)

Что делает:
1. Раскладывает уже скачанные веса (models/ рядом с кодом или в профиле)
   в раскладку CDN: <владелец>/<репозиторий>/<файл>. Файлы не копируются,
   сервер отдаёт их прямо с места.
2. Поднимает на 127.0.0.1 сервер с поддержкой Range, как у настоящего CDN.
3. Подменяет Hugging Face на заведомо мёртвый адрес — «заблокирован».
4. Качает каждую модель настоящим загрузчиком программы в пустую
   временную папку и сверяет sha256 с tools/модели.json.
5. Отдельно рвёт соединение посреди большого файла и проверяет, что
   загрузка продолжилась с места обрыва.

Боевые модели и база не трогаются: всё качается во временную папку.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
КОРЕНЬ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(КОРЕНЬ))

import app.asr.download as загрузка  # noqa: E402

СПИСОК = json.loads((КОРЕНЬ / "tools" / "модели.json").read_text(encoding="utf-8"))

# Где лежат модели у этого компьютера, по каталогу модели.
КАТАЛОГИ = {
    "istupakov/gigaam-v3-onnx": "gigaam-v3-e2e-rnnt",
    "onnx-community/whisper-small": "whisper-small",
    "onnx-community/whisper-base": "whisper-base",
    "beginning-ai/speechbrain-lang-id-voxlingua107-ecapa-onnx": "voxlingua",
    "Wespeaker/wespeaker-voxceleb-resnet34-LM": "wespeaker",
    "Xenova/multilingual-e5-small": "e5-small",
    "Qwen/Qwen3-1.7B-GGUF": "llm/qwen3-1.7b",
    "Qwen/Qwen3-4B-GGUF": "llm/qwen3-4b",
    "Qwen/Qwen3-8B-GGUF": "llm/qwen3-8b",
}


def где_модели() -> list[Path]:
    места = [КОРЕНЬ / "models"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        места.append(Path(appdata) / "Konspekt" / "models")
    return [м for м in места if м.is_dir()]


def найти_файл(repo: str, файл: str) -> Path | None:
    for место in где_модели():
        путь = место / КАТАЛОГИ[repo] / файл
        if путь.exists():
            return путь
    return None


def sha256(путь: Path) -> str:
    h = hashlib.sha256()
    with open(путь, "rb") as fh:
        for блок in iter(lambda: fh.read(1 << 20), b""):
            h.update(блок)
    return h.hexdigest()


class CDN(http.server.BaseHTTPRequestHandler):
    """Отдаёт файлы по раскладке CDN, с Range, как настоящий."""

    карта: dict[str, Path] = {}
    оборвать_после: dict[str, int] = {}
    запросы: list[tuple[str, int]] = []

    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self.do_GET(тело=False)

    def do_GET(self, тело: bool = True):
        путь = self.path.split("?", 1)[0]
        префикс = "/konspekt/"
        файл = self.карта.get(путь[len(префикс):]) if путь.startswith(префикс) else None
        if файл is None:
            self.send_error(404)
            return
        размер = файл.stat().st_size
        начало = 0
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            начало = int(rng[6:].split("-")[0] or 0)
        type(self).запросы.append((путь, начало))
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(размер - начало))
        self.send_header("Accept-Ranges", "bytes")
        if rng:
            self.send_header("Content-Range", f"bytes {начало}-{размер - 1}/{размер}")
        self.end_headers()
        if not тело:
            return
        предел = self.оборвать_после.pop(путь, None)
        отдано = 0
        try:
            with open(файл, "rb") as fh:
                fh.seek(начало)
                while блок := fh.read(1 << 20):
                    if предел is not None and отдано + len(блок) > предел:
                        self.wfile.write(блок[: предел - отдано])
                        self.wfile.flush()
                        self.connection.close()  # обрыв посреди файла
                        return
                    self.wfile.write(блок)
                    отдано += len(блок)
        except (ConnectionError, OSError):
            pass


def main() -> int:
    адрес = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else ""
    сервер = None
    карта: dict[str, Path] = {}
    есть_у_нас: list[tuple[str, str, int, str | None]] = []
    for м in СПИСОК:
        for f in м["files"]:
            if адрес:
                есть_у_нас.append((м["repo"], f["file"], f["size"], f["sha256"]))
                continue
            путь = найти_файл(м["repo"], f["file"])
            if путь is None or путь.stat().st_size != f["size"]:
                continue
            карта[f"{м['repo']}/{f['file']}"] = путь
            есть_у_нас.append((м["repo"], f["file"], f["size"], f["sha256"]))

    if not адрес:
        if not карта:
            print("Скачанных моделей не нашлось: сначала запустите программу и дайте им скачаться.")
            return 1
        CDN.карта = карта
        сервер = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CDN)
        threading.Thread(target=сервер.serve_forever, daemon=True).start()
        адрес = f"http://127.0.0.1:{сервер.server_address[1]}/konspekt"
        print(f"Свой «CDN» поднят: {адрес}  ({len(карта)} файлов из уже скачанных)")
    print("Hugging Face подменён на мёртвый адрес: как будто заблокирован.\n")

    os.environ[загрузка.ENV_BASE] = адрес
    загрузка.HF_BASE = "http://127.0.0.1:9/заблокирован/{repo}/{name}"
    загрузка.CDN_BASE = ""
    загрузка.RETRY_PAUSE = 0.2

    бед = 0
    with tempfile.TemporaryDirectory(prefix="konspekt-cdn-") as врем:
        куда = Path(врем)
        # Модели по одной, как их качает программа. Большие только если
        # это локальная проверка: гнать 10 ГБ с настоящего CDN не нужно.
        по_моделям: dict[str, list] = {}
        for repo, файл, размер, sha in есть_у_нас:
            по_моделям.setdefault(repo, []).append((файл, размер, sha))
        for repo, файлы in по_моделям.items():
            всего = sum(р for _, р, _ in файлы)
            if sys.argv[1:] and всего > 400_000_000:
                print(f"[пропуск] {repo}: {всего / 1e9:.1f} ГБ, большие качайте программой")
                continue
            начато = time.monotonic()
            dest = куда / repo.replace("/", "_")
            try:
                загрузка.ModelDownloader(repo, tuple(ф for ф, _, _ in файлы), dest).run_blocking()
            except Exception as e:  # noqa: BLE001
                print(f"[БЕДА] {repo}: {e}")
                бед += 1
                continue
            плохие = [ф for ф, р, sha in файлы
                      if (dest / ф).stat().st_size != р or (sha and sha256(dest / ф) != sha)]
            секунд = time.monotonic() - начато
            скорость = всего / max(секунд, 0.001) / 1e6
            if плохие:
                print(f"[БЕДА] {repo}: не сошлись {плохие}")
                бед += 1
            else:
                print(f"[ок] {repo}: {len(файлы)} файлов, {всего / 1e6:,.0f} МБ, {скорость:,.0f} МБ/с, sha256 сошёлся"
                      .replace(",", " "))
            for ф, _, _ in файлы:
                (dest / ф).unlink(missing_ok=True)

        # Обрыв посреди файла: только на своём «CDN», где мы им управляем.
        if сервер is not None:
            самый = max(карта.items(), key=lambda kv: kv[1].stat().st_size)
            путь_на_cdn = "/konspekt/" + самый[0]
            размер = самый[1].stat().st_size
            CDN.оборвать_после[путь_на_cdn] = размер // 3
            CDN.запросы.clear()
            repo, файл = самый[0].split("/", 2)[:2], самый[0].split("/", 2)[2]
            dest = куда / "обрыв"
            загрузка.ModelDownloader("/".join(repo), (файл,), dest).run_blocking()
            начала = [н for п, н in CDN.запросы if п == путь_на_cdn]
            целый = (dest / файл).stat().st_size == размер
            if целый and len(начала) >= 2 and начала[1] > 0:
                print(f"\n[ок] обрыв на трети {файл}: докачано с {начала[1]:,} байта, а не с нуля"
                      .replace(",", " "))
            else:
                print(f"\n[БЕДА] докачка после обрыва: запросы с байтов {начала}, файл целый: {целый}")
                бед += 1

    if сервер is not None:
        сервер.shutdown()
    print("\nГотово." if not бед else f"\nБед: {бед}")
    return 1 if бед else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Вложения к заметкам: файл на диске и запись о нём в базе.

Картинка из буфера обмена или файл с диска ложатся в
attachments/<встреча>/<номер>-<имя>: номер спереди, чтобы два файла с
одним именем не затирали друг друга. В базе имя, размер, путь и откуда
вложение: своё или с другого компьютера.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import paths
from .models import new_id

КАРТИНКИ = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
# Что окно умеет проиграть само: WebView2 это Edge, форматы его.
АУДИО = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
         ".oga": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac", ".aac": "audio/aac", ".weba": "audio/webm"}
ВИДЕО = {".mp4": "video/mp4", ".webm": "video/webm", ".m4v": "video/mp4", ".mov": "video/mp4"}
# Больше этого в окно данными не отдаём: строка base64 в полтора раза
# тяжелее файла и идёт через мост целиком.
МАКС_ВСТРОЙКА = 25 * 1024 * 1024
МИНИАТЮРА = 360


def вид(имя: str) -> str:
    суффикс = Path(имя).suffix.lower()
    if суффикс in КАРТИНКИ:
        return "image"
    if суффикс in АУДИО:
        return "audio"
    if суффикс in ВИДЕО:
        return "video"
    return "file"


def mime(имя: str) -> str:
    суффикс = Path(имя).suffix.lower()
    return АУДИО.get(суффикс) or ВИДЕО.get(суффикс) or {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
    }.get(суффикс, "application/octet-stream")


def миниатюра(путь: Path) -> str:
    """Картинка уменьшенной, data-URL. Без Pillow или на битом файле пусто."""
    import base64
    import io

    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(путь) as img:
            img.thumbnail((МИНИАТЮРА, МИНИАТЮРА))
            буфер = io.BytesIO()
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")
            img.save(буфер, format="PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(буфер.getvalue()).decode("ascii")
    except Exception:
        return ""


def данными(путь: Path) -> str:
    """Весь файл как data-URL для встроенного плеера. Пусто, если велик."""
    import base64

    try:
        if путь.stat().st_size > МАКС_ВСТРОЙКА:
            return ""
        return f"data:{mime(путь.name)};base64," + base64.b64encode(путь.read_bytes()).decode("ascii")
    except OSError:
        return ""
_ПЛОХИЕ = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def безопасное_имя(имя: str) -> str:
    имя = _ПЛОХИЕ.sub("_", (имя or "").strip()).strip(". ")
    return (имя or "файл")[:120]


def сохранить(store: Any, meeting_id: str, имя: str, данные: bytes,
              origin: str = "", id: str | None = None,
              created_at: float | None = None) -> dict[str, Any]:
    номер = id or new_id()
    имя = безопасное_имя(имя)
    папка = paths.attachments_dir() / meeting_id
    папка.mkdir(parents=True, exist_ok=True)
    путь = папка / f"{номер}-{имя}"
    путь.write_bytes(данные)
    return store.add_attachment(meeting_id, имя, str(путь), len(данные), origin, номер, created_at)


def для_окна(запись: dict[str, Any]) -> dict[str, Any]:
    путь = Path(запись["path"])
    есть = путь.exists()
    what = вид(запись["name"])
    return {
        "id": запись["id"], "name": запись["name"], "size": запись["size"],
        "created_at": запись["created_at"], "origin": запись.get("origin", ""),
        "kind": what, "exists": есть,
        # Картинка приходит сразу уменьшенной; звук и видео окно просит
        # отдельно, когда показывает, чтобы список не тянул мегабайты.
        "preview": миниатюра(путь) if есть and what == "image" else "",
        "embeddable": есть and what in ("audio", "video") and путь.stat().st_size <= МАКС_ВСТРОЙКА,
    }

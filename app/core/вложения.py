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
    return {
        "id": запись["id"], "name": запись["name"], "size": запись["size"],
        "created_at": запись["created_at"], "origin": запись.get("origin", ""),
        "image": путь.suffix.lower() in КАРТИНКИ,
        "url": путь.resolve().as_uri() if путь.exists() else "",
    }

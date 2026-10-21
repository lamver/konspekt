"""Расшифровка в файл: txt, markdown, srt, vtt, json.

Расшифровку несут дальше: в протокол, в субтитры к записи, в чужую
программу. У каждого адресата свой формат, и переписывать реплики руками
никто не станет. Поэтому форматы те, что понимают все: простой текст и
markdown для людей, SRT и WebVTT для видеоплееров и монтажа, JSON для
программ. Формат выбирается расширением файла в диалоге «Сохранить как».
"""

from __future__ import annotations

import json
from typing import Any

ФОРМАТЫ = ("txt", "md", "srt", "vtt", "json")


def формат_по_имени(имя: str) -> str:
    """Формат по расширению файла; незнакомое или пустое — текст."""
    хвост = имя.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in хвост:
        return "txt"
    расширение = хвост.rsplit(".", 1)[-1].lower()
    return расширение if расширение in ФОРМАТЫ else "txt"


def метка(секунды: float) -> str:
    """Время в расшифровке: мм:сс, после часа ч:мм:сс."""
    s = max(0, int(секунды))
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"


def _время_субтитров(секунды: float, разделитель: str) -> str:
    """чч:мм:сс,ммм для SRT (запятая) и чч:мм:сс.ммм для WebVTT (точка)."""
    мс = max(0, int(round(секунды * 1000)))
    return f"{мс // 3600000:02d}:{мс % 3600000 // 60000:02d}:{мс % 60000 // 1000:02d}{разделитель}{мс % 1000:03d}"


def _границы(строка: dict[str, Any]) -> tuple[float, float]:
    """Начало и конец реплики; субтитр без длительности плеер не покажет."""
    start = float(строка.get("start") or 0.0)
    end = float(строка.get("end") or 0.0)
    if end <= start:
        end = start + 1.0
    return start, end


def собрать(формат: str, название: str, строки: list[dict[str, Any]]) -> str:
    """Текст файла в нужном формате.

    `строки`: словари с `start`, `end`, `who` (подпись говорящего) и `text`.
    """
    if формат == "srt":
        куски = []
        for n, с in enumerate(строки, 1):
            a, b = _границы(с)
            куски.append(
                f"{n}\n{_время_субтитров(a, ',')} --> {_время_субтитров(b, ',')}\n"
                f"{с['who']}: {с['text']}\n"
            )
        return "\n".join(куски)
    if формат == "vtt":
        куски = ["WEBVTT\n"]
        for с in строки:
            a, b = _границы(с)
            куски.append(
                f"{_время_субтитров(a, '.')} --> {_время_субтитров(b, '.')}\n"
                f"<v {с['who']}>{с['text']}\n"
            )
        return "\n".join(куски)
    if формат == "json":
        return json.dumps(
            {
                "title": название,
                "segments": [
                    {
                        "start": round(float(с.get("start") or 0.0), 3),
                        "end": round(float(с.get("end") or 0.0), 3),
                        "speaker": с["who"],
                        "text": с["text"],
                    }
                    for с in строки
                ],
            },
            ensure_ascii=False, indent=2,
        ) + "\n"
    if формат == "md":
        куски = [f"# {название}\n"] if название else []
        for с in строки:
            куски.append(f"**{с['who']}** · {метка(с.get('start') or 0.0)}  \n{с['text']}\n")
        return "\n".join(куски)
    # txt
    куски = [f"{название}\n"] if название else []
    for с in строки:
        куски.append(f"[{метка(с.get('start') or 0.0)}] {с['who']}: {с['text']}")
    return "\n".join(куски) + "\n"

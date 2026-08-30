"""Сколько текста влезает в модель и как его резать.

Длинная встреча в окно контекста не помещается: полтора часа разговора
это больше тридцати тысяч токенов, а у локальной модели окно 32k. Без
нарезки сервер просто отвечает отказом, и человек остаётся без заметок
именно на той встрече, ради которой всё затевалось.

Токены считаем прикидкой, а не токенизатором: тащить его в билд ради
оценки длины дорого, а ошибка в обе стороны покрывается запасом.
"""

from __future__ import annotations

# Русский текст в среднем даёт примерно один токен на три символа.
# Берём с запасом в меньшую сторону, чтобы скорее перестраховаться.
CHARS_PER_TOKEN = 2.8


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def fits(text: str, budget_tokens: int) -> bool:
    return estimate_tokens(text) <= budget_tokens


def split_transcript(text: str, budget_tokens: int) -> list[str]:
    """Разбить расшифровку на куски, влезающие в бюджет.

    Режем по границам реплик: разрыв посреди фразы отнимает у модели
    и говорящего, и смысл сказанного.

    Одна реплика длиннее бюджета целиком (человек говорил без пауз
    двадцать минут) режется по словам: потерять её совсем хуже.
    """
    if fits(text, budget_tokens):
        return [text]

    budget_chars = int(budget_tokens * CHARS_PER_TOKEN)
    chunks: list[str] = []
    current: list[str] = []
    size = 0

    for line in text.split("\n"):
        line_size = len(line) + 1
        if line_size > budget_chars:
            if current:
                chunks.append("\n".join(current))
                current, size = [], 0
            chunks.extend(_split_long_line(line, budget_chars))
            continue
        if size + line_size > budget_chars and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += line_size

    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if c.strip()]


def _split_long_line(line: str, budget_chars: int) -> list[str]:
    out: list[str] = []
    words = line.split(" ")
    current: list[str] = []
    size = 0
    for word in words:
        if size + len(word) + 1 > budget_chars and current:
            out.append(" ".join(current))
            current, size = [], 0
        current.append(word)
        size += len(word) + 1
    if current:
        out.append(" ".join(current))
    return out

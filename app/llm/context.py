"""Что из встречи показать модели в ответ на вопрос.

Раньше в чат уходила вся расшифровка, а если не влезала, то её конец.
Это плохо сразу по трём причинам:

- маленькая модель тонет в длинном тексте и отвечает общими словами
  («Я помощник по встрече»), даже когда ответ в расшифровке есть;
- каждый вопрос заново прогоняет через модель десятки тысяч знаков, и
  на процессоре ответ ждётся минутами;
- на длинной встрече начало просто отрезалось, и вопрос про него
  оставался без ответа.

Теперь так. Расшифровка режется на куски теми же правилами, что и для
поиска по смыслу (search/meaning.py), поэтому векторы кусков уже лежат в
базе и считать их второй раз не нужно. К вопросу подбираются самые
близкие куски: по смыслу, если модель смысла скачана, и по словам
всегда. Каждый найденный кусок берётся вместе с соседями, чтобы модель
видела, к чему была сказана фраза. Отрывки идут в порядке встречи, с
отметкой времени.

Короткую встречу отдаём целиком: искать в десяти репликах незачем, а
целиком модель видит больше.

Вопрос вроде «+9» или «а он?» сам по себе ничего не ищет, поэтому ищем
по нему вместе с предыдущими вопросами переписки.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..search.meaning import chunk_segments
from ..search.ranking import основы, покрытие
from .chunking import estimate_tokens

# Встреча короче этого уходит в модель целиком. Около семи тысяч знаков:
# пятнадцать минут разговора. Столько маленькая модель ещё читает
# внимательно, а поиск по такой встрече только теряет связность.
WHOLE_TOKENS = 2500
# Сколько отрывков расшифровки отдаём на вопрос. Хватает на полдюжины
# мест встречи с соседями и оставляет модели место под переписку.
CONTEXT_TOKENS = 3500
# Сколько лучших кусков брать до добавления соседей.
TOP_CHUNKS = 6
# Соседей с каждой стороны найденного куска.
NEIGHBOURS = 1
# Сколько прошлых вопросов подмешивать в поиск. Больше — и поиск уводит
# в тему, о которой спрашивали десять минут назад.
HISTORY_QUESTIONS = 2
# Сколько последних сообщений переписки отдавать модели. Вся переписка
# за час обсуждения вытеснила бы саму встречу.
HISTORY_MESSAGES = 10
HISTORY_TOKENS = 1500
# Вес слов против смысла. Слова точнее на именах и числах («Лёвин»,
# «пятница»), смысл — на пересказе своими словами. Смысл у этой модели
# колеблется в узком коридоре около 0.8, поэтому его растягиваем.
WORDS_WEIGHT = 1.0
MEANING_WEIGHT = 1.0


@dataclass
class ChatContext:
    """Что пойдёт в модель вместе с вопросом."""

    transcript: str
    # Отдали всю встречу или только отрывки. Промпт говорит модели об
    # этом честно: «ответа в отрывках нет» и «ответа во встрече нет» —
    # разные вещи.
    whole: bool
    history: list[dict[str, str]] = field(default_factory=list)
    # Начала выбранных кусков, для проверок и журнала.
    starts: list[float] = field(default_factory=list)
    # Прикладывать ли встречу вообще. На «1+6» и «+9» модель, получив
    # рядом с вопросом отрывки, принималась пересказывать их вместо
    # счёта: проверено на копии живой базы.
    attach: bool = True


def clock(seconds: float) -> str:
    """Отметка времени в расшифровке: 7:05 или 1:02:03."""
    s = max(0, int(seconds))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def search_text(question: str, history: list[dict[str, Any]]) -> str:
    """По чему искать: вопрос и пара предыдущих вопросов человека."""
    прошлые = [
        (m.get("content") or "").strip()
        for m in history
        if m.get("role") == "user" and (m.get("content") or "").strip()
    ][-HISTORY_QUESTIONS:]
    return " ".join([*прошлые, question.strip()]).strip()


def trim_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Последние сообщения переписки, которые влезают в бюджет.

    Режем с начала: свежие реплики нужнее, по ним понятно, что значит
    «а он?». Первым в отданном куске должен быть вопрос человека, а не
    ответ модели, иначе переписка начинается с середины.
    """
    чистая = [
        {"role": m["role"], "content": (m.get("content") or "").strip()}
        for m in history
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ][-HISTORY_MESSAGES:]
    итог: list[dict[str, str]] = []
    размер = 0
    for m in reversed(чистая):
        размер += estimate_tokens(m["content"])
        if размер > HISTORY_TOKENS and итог:
            break
        итог.insert(0, m)
    while итог and итог[0]["role"] != "user":
        итог.pop(0)
    return итог


def _lines(segments: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{s['who']}: {s['text'].strip()}" for s in segments if (s.get("text") or "").strip()
    )


def build(
    segments: list[dict[str, Any]],
    question: str,
    history: list[dict[str, Any]],
    chunk_vectors: tuple[list[float], np.ndarray] | None = None,
    embed_query: Callable[[str], np.ndarray] | None = None,
) -> ChatContext:
    """Собрать то, что модель увидит вместе с вопросом.

    segments — реплики встречи по порядку: text, start, who.
    chunk_vectors — начала кусков и их векторы из указателя по смыслу.
    embed_query — посчитать вектор вопроса; None, если модели смысла нет.
    """
    истории = trim_history(history)
    if not any(ch.isalpha() for ch in question):
        # Ни одной буквы: «1+6», «+9», «?». Это счёт или продолжение
        # переписки, и встреча тут только мешает.
        return ChatContext(transcript="", whole=True, history=истории, attach=False)
    всё = _lines(segments)
    if estimate_tokens(всё) <= WHOLE_TOKENS:
        return ChatContext(transcript=всё, whole=True, history=истории)

    куски = chunk_segments(segments)
    if not куски:
        return ChatContext(transcript=всё, whole=True, history=истории)

    запрос = search_text(question, history)
    счёт = np.zeros(len(куски), dtype=np.float32)

    # Слова. Доля основ запроса, найденных в куске.
    осн = основы(запрос)
    for i, к in enumerate(куски):
        счёт[i] += WORDS_WEIGHT * покрытие(к["text"], осн)

    # Смысл. Векторы берём из указателя по началу куска: нарезка та же,
    # а если указатель устарел (встреча ещё пишется), кусок просто
    # остаётся без прибавки за смысл, а не получает чужой вектор.
    if chunk_vectors is not None and embed_query is not None and запрос:
        starts, vectors = chunk_vectors
        if len(starts) and vectors.size:
            try:
                q = embed_query(запрос)
                близость = vectors @ q
                по_началу = {round(s, 2): float(b) for s, b in zip(starts, близость)}
                значения = np.array(list(по_началу.values()), dtype=np.float32)
                среднее = float(значения.mean())
                разброс = float(значения.std()) or 1.0
                for i, к in enumerate(куски):
                    б = по_началу.get(round(float(к["start"]), 2))
                    if б is not None:
                        # Сколько разбросов кусок выше среднего по встрече.
                        # Сырая близость у всех кусков почти одинакова.
                        счёт[i] += MEANING_WEIGHT * max(0.0, (б - среднее) / разброс) / 3
            except Exception:
                # Смысл — прибавка, а не основа: без него ищем по словам.
                pass

    порядок = list(np.argsort(-счёт, kind="stable"))
    if float(счёт.max(initial=0.0)) <= 0.0:
        # Ни одного попадания: вопрос общий («о чём говорили?»). Тогда
        # полезнее всего начало и конец встречи, где обычно цель и итоги.
        n = len(куски)
        порядок = [*range(min(3, n)), *range(max(0, n - 3), n)]

    # Лучшие куски по очереди, каждый с соседями, пока не кончится
    # место. Кусок, который целиком не влезает, пропускаем, а не режем:
    # обрывок фразы модель поймёт хуже, чем её отсутствие.
    выбрано: set[int] = set()
    размер = 0
    for i in порядок[:TOP_CHUNKS]:
        for j in range(i - NEIGHBOURS, i + NEIGHBOURS + 1):
            if not 0 <= j < len(куски) or j in выбрано:
                continue
            прибавка = estimate_tokens(куски[j]["text"]) + 4
            if размер + прибавка > CONTEXT_TOKENS and выбрано:
                continue
            выбрано.add(j)
            размер += прибавка

    части: list[str] = []
    прошлый = -2
    for j in sorted(выбрано):
        if j != прошлый + 1 and части:
            части.append("…")
        к = куски[j]
        # Каждая реплика со своим говорящим: на «кто что взял на себя»
        # склеенный кусок без имён ответа не даёт.
        строки = к.get("lines") or [(к.get("who", ""), к["text"])]
        for n, (who, text) in enumerate(строки):
            метка = f"[{clock(к['start'])}] " if n == 0 else ""
            who = (who or "").strip()
            части.append(f"{метка}{who + ': ' if who else ''}{text}")
        прошлый = j
    return ChatContext(
        transcript="\n".join(части),
        whole=False,
        history=истории,
        starts=[float(куски[j]["start"]) for j in sorted(выбрано)],
    )

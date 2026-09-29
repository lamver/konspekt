"""Поиск по смыслу поверх поиска по словам.

Как устроено.

1. Расшифровка встречи режется на куски из соседних реплик, примерно по
   300 знаков. Отдельная реплика в среднем 47 знаков, и «Вот.» или
   «Завтра вечер.» смысла не несут; в куске они складываются в разговор.
2. Для каждого куска модель считает вектор, он лежит в базе. Считается
   фоном, свежие встречи первыми, и только то, что изменилось.
3. Запрос тоже становится вектором. Близость с каждым куском — одно
   умножение матрицы, на полутора тысячах кусков это миллисекунды.
4. Выдача общая. Совпадения по словам идут первыми: если человек написал
   слово, которое было сказано, это самый точный ответ. Смысл добавляет
   встречи, где слова другие, а тема та же, и только если близость выше
   порога: иначе на запрос «когда релиз» приехали бы встречи про погоду.

Всё это работает без сети и без сервера: модель лежит у человека.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

# Размер куска. Больше — теряется точность: в куске смешиваются две
# темы. Меньше — снова одиночные «угу». 300 знаков выбраны на настоящем
# архиве, при этом кусок укладывается в модель целиком.
CHUNK_CHARS = 300
# Кусок короче этого не считаем: в архиве такие состоят из «а.» и «Д»,
# и модель находит их близкими к любому запросу.
MIN_CHUNK_CHARS = 60
# Два порога, и нужны оба. Подобраны на живом архиве в 1488 кусков.
#
# Близость сама по себе плохо отделяет находку от шума: у этой модели
# все тексты похожи друг на друга на 0.82-0.86, это её свойство.
# «Верификация» находит нужное с близостью 0.856, а «когда релиз»
# находит афишу концерта с 0.849 — разница в третьем знаке.
#
# Поэтому второй порог — насколько кусок выделяется среди остальных:
# сколько раз разброс близостей по архиву укладывается между ним и
# средним. Настоящая находка стоит особняком (у «верификации» 5.7), а
# шум сливается с толпой (у «кто за что отвечает» лучшее всего 3.1).
MIN_SCORE = 0.85
MIN_STANDOUT = 3.0
# Сколько встреч добавлять по смыслу. Больше в список не влезает и не
# читается: нужны лучшие, а не все хоть чуть-чуть похожие.
MAX_MEETINGS = 8


def chunk_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Разрезать расшифровку встречи на куски из соседних реплик.

    На входе реплики по порядку, на выходе куски с началом первой
    реплики (чтобы клик открыл нужное место), говорящим этой реплики и
    склеенным текстом.
    """
    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    size = 0

    def flush() -> None:
        if not current:
            return
        text = " ".join(s["text"].strip() for s in current if s["text"].strip())
        if len(text) >= MIN_CHUNK_CHARS:
            chunks.append({
                "start": float(current[0]["start"]),
                "who": current[0].get("who", ""),
                "text": text,
            })

    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if size >= CHUNK_CHARS:
            flush()
            current, size = [], 0
        current.append(seg)
        size += len(text) + 1
    flush()
    return chunks


class MeaningIndex:
    """Указатель по смыслу: держит векторы свежими и ищет по ним."""

    def __init__(
        self, store, model,
        who: Callable[[Any], str] | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        self.store = store
        self.model = model
        self._who = who or (lambda seg: "")
        # Сообщить окну, что указатель пополнился: открытый поиск надо
        # переспросить, иначе новое не появится, пока не перепечатаешь.
        self._on_done = on_done
        # Пересчёт идёт в одном потоке. Второй запрос на пересчёт, пока
        # первый работает, просто просит его пройтись ещё раз.
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = False
        # Матрица кусков в памяти. Сбрасывается после каждого пересчёта:
        # читать из базы на каждую букву запроса незачем.
        self._cache: tuple[list[dict[str, Any]], np.ndarray] | None = None
        self._cache_lock = threading.Lock()

    # --- состояние ------------------------------------------------------

    @property
    def ready(self) -> bool:
        return self.model.is_downloaded()

    def status(self) -> dict[str, Any]:
        done, total = self.store.meaning_progress(self.model.name)
        return {
            "downloaded": self.model.is_downloaded(),
            "indexing": self._busy,
            "done": done,
            "total": total,
        }

    # --- пересчёт -------------------------------------------------------

    def start(self) -> None:
        """Поднять фоновый пересчёт и сразу пройтись по архиву."""
        if self._thread and self._thread.is_alive():
            self.refresh()
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="meaning-index", daemon=True)
        self._thread.start()
        self.refresh()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def refresh(self) -> None:
        """Попросить пересчитать изменившееся. Не ждёт окончания."""
        self._wake.set()

    def forget(self, meeting_id: str) -> None:
        """Встречу удалили: её куски не должны больше находиться.

        Из базы они ушли вместе со встречей, но матрица в памяти ещё
        помнит их, и без этого удалённое находилось бы до следующего
        пересчёта.
        """
        with self._cache_lock:
            self._cache = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                return
            if not self.model.is_downloaded():
                continue
            changed = 0
            try:
                self._busy = True
                changed = self.update_all()
            except Exception:
                log.exception("Пересчёт указателя по смыслу не удался")
            finally:
                self._busy = False
            if changed and self._on_done:
                try:
                    self._on_done()
                except Exception:
                    log.exception("Не удалось сообщить о пересчёте указателя")

    def update_all(self) -> int:
        """Досчитать всё, что устарело. Вернуть число пересчитанных встреч."""
        todo = self.store.meetings_needing_meaning(self.model.name)
        if not todo:
            return 0
        began = time.monotonic()
        log.info("Поиск по смыслу: пересчитываем встреч %d", len(todo))
        for meeting_id in todo:
            if self._stop.is_set():
                break
            self.update_meeting(meeting_id)
        log.info("Поиск по смыслу: готово за %.1f с", time.monotonic() - began)
        return len(todo)

    def update_meeting(self, meeting_id: str) -> None:
        # Отпечаток снимаем до чтения реплик: если пока считаем, придёт
        # новая, отпечаток не сойдётся, и встреча пересчитается ещё раз.
        # Наоборот вышло бы, что новая реплика навсегда осталась мимо.
        signature = self.store.meaning_signature(meeting_id)
        segments = [
            {"text": s.text, "start": s.start, "who": self._who(s)}
            for s in self.store.list_segments(meeting_id)
        ]
        chunks = chunk_segments(segments)
        vectors = self.model.passages([c["text"] for c in chunks])
        self.store.save_meaning(meeting_id, self.model.name, signature, chunks, vectors)
        with self._cache_lock:
            self._cache = None

    # --- поиск ----------------------------------------------------------

    def _matrix(self) -> tuple[list[dict[str, Any]], np.ndarray]:
        with self._cache_lock:
            if self._cache is None:
                self._cache = self.store.list_meaning(self.model.name)
            return self._cache

    def search(self, query: str) -> list[dict[str, Any]]:
        """Куски, близкие к запросу по смыслу, лучшие первыми.

        Пустой список, а не ошибка, если модели нет или указатель пуст:
        поиск по словам работает и без него, и ронять его из-за смысла
        нельзя.
        """
        query = (query or "").strip()
        if len(query) < 2 or not self.model.is_downloaded():
            return []
        items, vectors = self._matrix()
        if not items:
            return []
        try:
            q = self.model.query(query)
        except Exception:
            log.exception("Не удалось посчитать смысл запроса")
            return []
        scores = vectors @ q
        return pick(items, scores)


def pick(items: list[dict[str, Any]], scores: np.ndarray) -> list[dict[str, Any]]:
    """Отобрать куски, которые и близки к запросу, и выделяются из архива.

    Отдельной функцией, чтобы пороги можно было проверить на выдуманных
    числах, без модели: ошибку в отборе видно только по качеству
    выдачи, а его глазами не проверишь.
    """
    if len(scores) == 0:
        return []
    spread = float(scores.std())
    mean = float(scores.mean())
    found = []
    for i in np.argsort(-scores):
        score = float(scores[i])
        if score < MIN_SCORE:
            break
        # На крошечном архиве разброса нет, и выделяться не из чего:
        # тогда хватает одной близости.
        standout = (score - mean) / spread if spread > 1e-6 else MIN_STANDOUT
        if standout < MIN_STANDOUT:
            break
        found.append({**items[i], "score": score, "standout": standout})
    return found


def merge(
    by_words: list[dict[str, Any]], by_meaning: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Сложить выдачу по словам и по смыслу в один список встреч.

    Встречи, найденные словами, идут первыми и в своём порядке: точное
    совпадение надёжнее похожего. Если такая встреча нашлась и по смыслу,
    второй раз её не показываем. Встречи, найденные только по смыслу,
    идут следом, самые близкие первыми, и помечены: человек должен
    понимать, почему в цитате нет его слова.
    """
    result = list(by_words)
    seen = {m["meeting_id"] for m in result}
    added: dict[str, dict[str, Any]] = {}
    for hit in by_meaning:
        mid = hit["meeting_id"]
        if mid in seen:
            continue
        item = added.get(mid)
        if item is None:
            if len(added) >= MAX_MEETINGS:
                continue
            item = added[mid] = {
                "meeting_id": mid,
                "title": hit["title"],
                "created_at": hit["created_at"],
                "hits": 0,
                "quotes": [],
                "by_meaning": True,
                "score": hit["score"],
            }
        item["hits"] += 1
        if len(item["quotes"]) < 3:
            item["quotes"].append({
                "text": hit["text"],
                "start": hit["start_s"],
                "who": hit["who"],
            })
    result.extend(sorted(added.values(), key=lambda m: -m["score"]))
    return result

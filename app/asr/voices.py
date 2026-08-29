"""Кто говорит: разбор голосов внутри дорожки.

Задача. Дорожка «они» это один смешанный поток: в звонке по очереди
говорят несколько человек, и все их реплики приходят под одной меткой.
Дорожка «я» тоже не гарантирует одного человека, за компьютером может
сидеть несколько. Здесь мы группируем фразы по голосам.

Как. Каждая фраза приходит отпечатком (см. embedder.py). Новую фразу
сравниваем со всеми известными голосами встречи: если похожа на кого-то
сильнее порога, это он; если нет, заводим нового участника. Обычная
жадная онлайн-кластеризация, и выбрана она осознанно: реплики надо
показывать сразу во время встречи, а не после её конца, поэтому все
красивые методы, которым нужна вся запись целиком, нам не подходят.

Голос участника это не первый отпечаток, а среднее всех его фраз. Одна
неудачная фраза (кашель, короткое «угу») тогда не сдвигает эталон.

Пороги. Косинусная близость у WeSpeaker для одного человека обычно выше
0.6, для разных людей ниже 0.4. Между ними серая зона, и в ней мы
осознанно выбираем «это новый участник»: слить двух людей в одного
хуже, чем показать одного человека дважды, потому что первое чинится
только переслушиванием, а второе одним переименованием.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

import numpy as np

from .embedder import cosine

log = logging.getLogger(__name__)

# Порог «это тот же самый человек».
SAME_VOICE = 0.55
# Порог для участника, у которого набралась всего пара фраз. Его эталон
# это ещё почти одиночный отпечаток со всем случайным разбросом, какой
# бывает у отдельной фразы, и до общего порога такой участник просто не
# дотягивает: на ровном месте появлялся лишний «Собеседник». Разные люди
# дают близость около нуля, так что запас до ошибки тут всё равно велик.
NEW_VOICE = 0.42
# Сколько фраз считаем «мало».
FEW_SAMPLES = 3
# Порог узнавания по базе прошлых встреч. Строже: ошибиться именем
# знакомого человека неприятнее, чем не узнать его.
KNOWN_VOICE = 0.62
# Сколько отпечатков держим на участника, чтобы усреднять эталон.
MAX_SAMPLES = 20


@dataclass
class Voice:
    """Один говорящий внутри встречи."""

    id: str
    track: str                     # "me" или "them": откуда шёл звук
    label: str                     # то, что видит человек
    person_id: str | None = None   # ссылка в базу голосов, если узнали
    samples: int = 0
    _sum: np.ndarray | None = None

    @property
    def centroid(self) -> np.ndarray | None:
        """Средний отпечаток участника."""
        if self._sum is None or self.samples == 0:
            return None
        vec = self._sum / self.samples
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 1e-6 else None

    def add(self, vec: np.ndarray) -> None:
        if self._sum is None:
            self._sum = np.array(vec, dtype=np.float32)
        elif self.samples < MAX_SAMPLES:
            self._sum = self._sum + vec
        else:
            # Дальше копить незачем: эталон уже устойчив, а свежие фразы
            # пусть слегка подправляют его, не перевешивая историю.
            self._sum = self._sum * 0.95 + vec * 0.05
            return
        self.samples += 1


class VoiceRoster:
    """Участники текущей встречи и распределение фраз по ним."""

    def __init__(
        self,
        same_threshold: float = SAME_VOICE,
        known_threshold: float = KNOWN_VOICE,
        new_threshold: float = NEW_VOICE,
    ) -> None:
        self.same_threshold = same_threshold
        self.known_threshold = known_threshold
        self.new_threshold = new_threshold
        self._voices: dict[str, Voice] = {}
        self._counter = 0
        # Фразы приходят из потока распознавания, а UI может читать состав
        # участников в любой момент.
        self._lock = threading.Lock()
        # Эталоны известных людей: person_id -> (имя, вектор).
        self._known: dict[str, tuple[str, np.ndarray]] = {}

    # --- известные голоса ------------------------------------------------

    def remember(self, person_id: str, name: str, vector: np.ndarray) -> None:
        """Добавить эталон из базы голосов, чтобы узнавать между встречами."""
        vec = np.asarray(vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec))
        if norm < 1e-6:
            return
        with self._lock:
            self._known[person_id] = (name, vec / norm)

    def forget_all(self) -> None:
        with self._lock:
            self._known.clear()

    # --- разбор ----------------------------------------------------------

    def assign(self, track: str, vector: np.ndarray | None) -> Voice | None:
        """Определить, кому принадлежит фраза.

        None на входе (короткая фраза, модель не скачана) даёт None на
        выходе: реплика останется просто дорожкой, как было раньше.
        """
        if vector is None:
            return None
        vec = np.asarray(vector, dtype=np.float32).reshape(-1)
        if vec.size == 0:
            return None

        with self._lock:
            best, score = self._closest_locked(track, vec)
            if best is not None and score >= self._threshold_for(best):
                best.add(vec)
                return best
            voice = self._create_locked(track, vec)
            return voice

    def _threshold_for(self, voice: Voice) -> float:
        """Насколько похожей должна быть фраза, чтобы считаться его.

        У новичка эталон построен на одной-двух фразах и сам по себе
        разболтан, поэтому спрашиваем с него мягче. Как только фраз
        набирается достаточно, переходим на обычный порог.
        """
        if voice.samples < FEW_SAMPLES:
            return self.new_threshold
        return self.same_threshold

    def _closest_locked(self, track: str, vec: np.ndarray) -> tuple[Voice | None, float]:
        """Ближайший участник той же дорожки.

        Сравниваем только внутри дорожки: голос из микрофона и тот же
        голос из динамика звучат по-разному (кодек связи, обработка), и
        сводить их вместе — верный способ ошибиться.
        """
        best: Voice | None = None
        best_score = -1.0
        for voice in self._voices.values():
            if voice.track != track:
                continue
            centroid = voice.centroid
            if centroid is None:
                continue
            score = cosine(vec, centroid)
            if score > best_score:
                best, best_score = voice, score
        return best, best_score

    def _create_locked(self, track: str, vec: np.ndarray) -> Voice:
        """Новый участник. Если голос знаком по базе, сразу с именем."""
        person_id, name, score = None, "", -1.0
        for pid, (pname, pvec) in self._known.items():
            s = cosine(vec, pvec)
            if s > score:
                person_id, name, score = pid, pname, s
        recognised = person_id is not None and score >= self.known_threshold
        # Один и тот же человек не может быть двумя участниками встречи.
        if recognised and any(v.person_id == person_id for v in self._voices.values()):
            recognised = False

        self._counter += 1
        voice = Voice(
            id=f"{track}-{self._counter}",
            track=track,
            label=name if recognised else self._default_label(track),
            person_id=person_id if recognised else None,
        )
        voice.add(vec)
        self._voices[voice.id] = voice
        if recognised:
            log.info("Узнали голос: %s (близость %.2f)", name, score)
        else:
            log.info("Новый голос: %s", voice.label)
        return voice

    def _default_label(self, track: str) -> str:
        """Имя по умолчанию: нумеруем внутри дорожки, а не сквозным счётчиком."""
        n = sum(1 for v in self._voices.values() if v.track == track) + 1
        if track == "me":
            return "Я" if n == 1 else f"Голос {n}"
        return f"Собеседник {n}"

    # --- состав ----------------------------------------------------------

    def voices(self) -> list[Voice]:
        with self._lock:
            return list(self._voices.values())

    def get(self, voice_id: str) -> Voice | None:
        with self._lock:
            return self._voices.get(voice_id)

    def rename(self, voice_id: str, label: str) -> bool:
        with self._lock:
            voice = self._voices.get(voice_id)
            if voice is None:
                return False
            voice.label = label
            return True

    def reset(self) -> None:
        with self._lock:
            self._voices.clear()
            self._counter = 0

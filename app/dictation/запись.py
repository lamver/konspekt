"""Запись голоса для диктовки: одна дорожка, только в память.

Почему не переиспользуем `AudioCapture` встречи. У неё другая работа:
две дорожки, два WAV-файла на диске, строка в базе, поиск говорящего,
саммари в конце. Диктовке ничего этого не нужно, а нужно ей ровно
обратное — начать писать в тот же миг, когда человек нажал клавишу, и
отдать звук сразу, как он отпустил.

Файл на диск не пишется вовсе. Наговоренное в чужое окно это не встреча,
хранить его негде и незачем: человек диктует пароль, адрес, личное
сообщение, и оставлять это лежать в папке было бы предательством
обещания приватности.

Ограничение по длине не каприз: если клавиша залипнет или человек про
неё забудет, запись без предела съест память. Пять минут диктовки это
уже не диктовка, а встреча.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from ..audio import devices
from ..audio.buffers import SAMPLE_RATE, float_to_int16, rms_level

log = logging.getLogger(__name__)

# Сколько кадров просим у карты за раз. 0.05 с вместо 0.1 с у встречи:
# для диктовки важна каждая десятая доля, человек ждёт свой текст.
БЛОК_КАДРОВ = 800

# Дольше этого не пишем. Клавиша может залипнуть, а человек — отвлечься,
# и без предела запись съест всю память.
ПРЕДЕЛ_СЕКУНД = 300.0

# Тише этого считаем тишиной при обрезке краёв. Не порог речи, а именно
# «здесь ничего нет»: строже нельзя, иначе срежется тихое начало слова.
ПОРОГ_ТИШИНЫ = 0.008


class ЗахватГолоса:
    """Пишет микрофон в память, пока его не остановят."""

    def __init__(self, device_id: str = "") -> None:
        self.device_id = device_id
        self._поток: threading.Thread | None = None
        self._стоп = threading.Event()
        self._куски: list[np.ndarray] = []
        self._замок = threading.Lock()
        self._уровень = 0.0
        self._ошибка: str | None = None
        self._начал = threading.Event()

    @property
    def идёт_запись(self) -> bool:
        return self._поток is not None and self._поток.is_alive()

    @property
    def уровень(self) -> float:
        """Громкость для индикатора: человек должен видеть, что его слышно."""
        return self._уровень

    @property
    def ошибка(self) -> str | None:
        return self._ошибка

    def старт(self, ждать: float = 2.0) -> bool:
        """Начать запись. Возвращает False, если микрофон не открылся.

        Ждём подтверждения, что дорожка действительно пошла: иначе
        человек говорит в закрытый микрофон и узнаёт об этом только по
        пустому результату.
        """
        if self.идёт_запись:
            return True
        self._стоп.clear()
        self._начал.clear()
        self._ошибка = None
        with self._замок:
            self._куски = []
        self._поток = threading.Thread(
            target=self._работа, name="диктовка-микрофон", daemon=True
        )
        self._поток.start()
        return self._начал.wait(ждать) and self._ошибка is None

    def стоп(self) -> np.ndarray:
        """Остановить запись и забрать наговоренное одним куском."""
        self._стоп.set()
        поток, self._поток = self._поток, None
        if поток is not None:
            # Ждём чуть дольше блока, чтобы забрать последний кусок:
            # оборвать поток сразу значит потерять хвост фразы.
            поток.join(timeout=1.5)
        self._уровень = 0.0
        with self._замок:
            куски, self._куски = self._куски, []
        if not куски:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(куски)

    def _работа(self) -> None:
        try:
            микрофон = devices.open_microphone(self.device_id or None)
        except Exception as exc:
            self._ошибка = f"микрофон не открылся: {exc}"
            log.warning("Диктовка: %s", self._ошибка)
            self._начал.set()
            return

        try:
            with микрофон.recorder(samplerate=SAMPLE_RATE, channels=1,
                                   blocksize=БЛОК_КАДРОВ) as запись:
                self._начал.set()
                предел = time.monotonic() + ПРЕДЕЛ_СЕКУНД
                while not self._стоп.is_set():
                    данные = запись.record(numframes=БЛОК_КАДРОВ)
                    моно = np.asarray(данные, dtype=np.float32).reshape(-1)
                    self._уровень = rms_level(моно)
                    with self._замок:
                        self._куски.append(float_to_int16(моно))
                    if time.monotonic() > предел:
                        log.warning(
                            "Диктовка идёт дольше %.0f с, останавливаем сами",
                            ПРЕДЕЛ_СЕКУНД,
                        )
                        break
        except Exception as exc:
            self._ошибка = f"запись прервалась: {exc}"
            log.warning("Диктовка: %s", self._ошибка, exc_info=True)
            self._начал.set()


def обрезать_тишину(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Убрать тишину по краям записи.

    Человек нажимает клавишу до того, как начал говорить, и отпускает
    после того, как закончил: полсекунды пустоты с каждого края почти
    неизбежны. Модели это лишняя работа, а на длинной тишине она ещё и
    выдумывает слова.

    Режем только края. Паузы внутри трогать нельзя: между словами тоже
    тихо, и вычистив их, мы склеим речь в кашу.
    """
    if pcm.size == 0:
        return pcm
    кадр = max(1, int(0.02 * sample_rate))
    громкость = np.array([
        float(np.sqrt(np.mean(np.square(
            pcm[начало:начало + кадр].astype(np.float64) / 32768.0
        ))))
        for начало in range(0, pcm.size, кадр)
    ])
    звучит = np.flatnonzero(громкость > ПОРОГ_ТИШИНЫ)
    if звучит.size == 0:
        # Совсем тихо: вернём пустоту, распознавать тут нечего.
        return np.zeros(0, dtype=pcm.dtype)

    # Прихватываем по кадру с каждой стороны: обрезка впритык съедает
    # начало первого звука, и «привет» превращается в «ривет».
    первый = max(0, int(звучит[0]) - 1) * кадр
    последний = min(громкость.size, int(звучит[-1]) + 2) * кадр
    return pcm[первый:последний]

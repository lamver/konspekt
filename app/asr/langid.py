"""Какой язык звучит во фразе.

Зачем. GigaAM знает только русский, и английскую речь он не отвергает, а
записывает кириллицей: «hello this is the first sentence» превращается в
«холло дис из зе фест сентинс». На двуязычной встрече такой транскрипт
бесполезен. Определив язык, мы можем отдать фразу подходящей модели.

Почему по звуку, а не по тексту. Текст к этому моменту уже испорчен: по
строке «холло дис из» ни один определитель языка не поймёт, что это
английский. Звук же несёт язык прямо в себе.

Модель. VoxLingua107 ECAPA, 85 МБ в ONNX, 107 языков, на процессоре
работает за десятки миллисекунд.

Осторожность с короткими фразами. На «ага» и «угу» модель угадывает
язык почти случайно, поэтому такие куски мы не спрашиваем вовсе, а
наследуем язык предыдущей фразы того же говорящего: человек редко
переключает язык посреди реплики.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

MODEL_REPO = "beginning-ai/speechbrain-lang-id-voxlingua107-ecapa-onnx"
MODEL_FILES = ("model.onnx", "labels.json")
MODEL_DIR_NAME = "voxlingua"
MODEL_TOTAL_BYTES = 85_414_707

SAMPLE_RATE = 16000
# Короче этого фразу не спрашиваем: ответ будет случайным.
MIN_SECONDS = 1.2
# Ниже этой уверенности ответу не доверяем: пусть язык останется прежним.
# Порог не из головы: на записях настоящих встреч верные ответы идут с
# уверенностью 0.67-0.98, а единственный неверный («португальский» вместо
# русского) набрал 0.43. Ошибка языка дороже отсутствия ответа, потому что
# уводит фразу в чужую модель распознавания.
MIN_CONFIDENCE = 0.5

# Языки, которые для нас равны русскому: они кириллические и звучат
# близко, VoxLingua регулярно называет русскую речь украинской. Отправлять
# их всё равно некуда, кроме GigaAM.
CYRILLIC_LANGS = frozenset({"ru", "uk", "be", "bg", "mk", "sr"})

# Признаки, которых ждёт модель.
N_MELS = 60
FRAME_LENGTH = 400   # 25 мс
FRAME_SHIFT = 160    # 10 мс
# Окно преобразования равно длине кадра: так считает speechbrain, и от
# этого зависит, куда попадут границы мел-полос.
N_FFT = 400


class LangIdMissing(RuntimeError):
    """Весов нет на диске."""


class LanguageDetector:
    """Определяет язык куска речи."""

    name = "voxlingua107-ecapa"

    def __init__(self, model_dir: Path, auto_load: bool = True) -> None:
        self.model_dir = Path(model_dir)
        self.auto_load = auto_load
        self._session: Any = None
        self._labels: list[str] = []
        self._lock = threading.Lock()
        self._load_failed: str | None = None
        self._mel_basis: np.ndarray | None = None

    def is_downloaded(self) -> bool:
        return all((self.model_dir / f).exists() for f in MODEL_FILES)

    @property
    def is_loaded(self) -> bool:
        return self._session is not None

    def load(self) -> None:
        with self._lock:
            if self._session is not None:
                return
            if not self.is_downloaded():
                raise LangIdMissing(f"Нет файлов {MODEL_FILES} в {self.model_dir}")
            import onnxruntime as ort

            log.info("Загружаем определитель языка из %s", self.model_dir)
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._session = ort.InferenceSession(
                str(self.model_dir / "model.onnx"), opts,
                providers=["CPUExecutionProvider"],
            )
            raw = json.loads((self.model_dir / "labels.json").read_text("utf-8"))
            self._labels = [item["language"] for item in raw]
            log.info("Определитель языка загружен, языков: %d", len(self._labels))

    def unload(self) -> None:
        with self._lock:
            self._session = None

    def detect(self, pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> tuple[str, float] | None:
        """Код языка и уверенность, либо None.

        None это нормальный ответ: слишком короткая фраза, невнятный
        ответ модели или её отсутствие не должны ломать распознавание.
        """
        if self._session is None:
            if not self.auto_load or self._load_failed:
                return None
            try:
                self.load()
            except Exception as exc:
                self._load_failed = str(exc)
                log.error("Определитель языка не загрузился: %s", exc)
                return None

        wave = _to_float32(pcm)
        if wave.size < int(MIN_SECONDS * sample_rate):
            return None

        try:
            feats = self._features(wave, sample_rate)
            if feats.shape[0] < 20:
                return None
            probs = self._session.run(
                None,
                {
                    "features": feats[None, :, :],
                    "wav_lens": np.array([1.0], dtype=np.float32),
                },
            )[0]
            probs = np.asarray(probs).reshape(-1)
        except Exception:
            log.exception("Не удалось определить язык")
            return None

        if probs.size != len(self._labels):
            log.warning("Модель вернула %d значений на %d языков",
                        probs.size, len(self._labels))
            return None
        # Модель отдаёт логарифмы вероятностей, приводим к обычным.
        if probs.max() <= 0.0:
            probs = np.exp(probs)
        total = float(probs.sum())
        if total > 0:
            probs = probs / total
        best = int(np.argmax(probs))
        confidence = float(probs[best])
        if confidence < MIN_CONFIDENCE:
            # Неуверенный ответ хуже, чем никакой: он уведёт фразу в чужую
            # модель распознавания, и текст будет испорчен целиком.
            return None
        return self._labels[best], confidence

    def is_russian(self, pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bool | None:
        """Годится ли фраза для GigaAM. None означает «не знаю».

        Отдельный метод, потому что нам нужен не язык как таковой, а
        решение о модели. VoxLingua путает русский с украинским и
        белорусским: они близки и на шумной записи модель выбирает соседа.
        Для нас все они одинаково кириллические и звучат по-славянски,
        так что в GigaAM их и отправляем.
        """
        result = self.detect(pcm, sample_rate)
        if result is None:
            return None
        return result[0] in CYRILLIC_LANGS

    # --- признаки --------------------------------------------------------

    def _features(self, wave: np.ndarray, sample_rate: int) -> np.ndarray:
        """Мел-фильтр-банк на 60 полос в децибелах.

        Две тонкости, каждая из которых по отдельности ломает ответ до
        случайного. Первая: масштаб. Модель обучена на децибелах, то есть
        на 10*log10, а не на натуральном логарифме; с натуральным она
        уверенно называет английскую речь валлийской. Вторая: фильтры
        строятся по настоящим частотам бинов, а не по округлённым до целого
        индексам, иначе все полосы уезжают.

        Нормализацию признаков модель делает сама, внутри графа, так что
        снаружи её повторять не нужно.
        """
        if sample_rate != SAMPLE_RATE:
            wave = _resample(wave, sample_rate, SAMPLE_RATE)

        n_frames = 1 + (len(wave) - FRAME_LENGTH) // FRAME_SHIFT
        if n_frames < 1:
            return np.zeros((0, N_MELS), dtype=np.float32)

        idx = (np.arange(FRAME_LENGTH)[None, :]
               + FRAME_SHIFT * np.arange(n_frames)[:, None])
        frames = wave[idx] * np.hanning(FRAME_LENGTH + 1)[:-1]
        spectrum = np.abs(np.fft.rfft(frames, N_FFT)) ** 2
        if self._mel_basis is None:
            self._mel_basis = _mel_basis(SAMPLE_RATE, N_FFT, N_MELS)
        mel = spectrum @ self._mel_basis.T
        return (10.0 * np.log10(np.maximum(mel, 1e-10))).astype(np.float32)


def _hz_to_mel(hz):
    return 2595.0 * np.log10(1.0 + np.asarray(hz) / 700.0)


def _mel_to_hz(mel):
    return 700.0 * (10.0 ** (np.asarray(mel) / 2595.0) - 1.0)


def _mel_basis(sample_rate: int, n_fft: int, n_mels: int) -> np.ndarray:
    """Треугольные фильтры по настоящим частотам бинов.

    Именно по частотам, а не по округлённым индексам: округление сдвигает
    полосы, и модель начинает угадывать язык вместо того, чтобы слышать.
    """
    hz_points = _mel_to_hz(
        np.linspace(_hz_to_mel(0.0), _hz_to_mel(sample_rate / 2.0), n_mels + 2)
    )
    bin_hz = np.linspace(0.0, sample_rate / 2.0, n_fft // 2 + 1)
    basis = np.zeros((n_mels, bin_hz.size), dtype=np.float32)
    for m in range(n_mels):
        left, centre, right = hz_points[m], hz_points[m + 1], hz_points[m + 2]
        up = (bin_hz - left) / max(centre - left, 1e-9)
        down = (right - bin_hz) / max(right - centre, 1e-9)
        basis[m] = np.maximum(0.0, np.minimum(up, down))
    return basis


def _resample(wave: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or wave.size == 0:
        return wave
    n = int(round(wave.size * dst / src))
    return np.interp(
        np.linspace(0, wave.size - 1, n, dtype=np.float32),
        np.arange(wave.size, dtype=np.float32),
        wave,
    ).astype(np.float32)


def _to_float32(pcm) -> np.ndarray:
    if isinstance(pcm, (bytes, bytearray)):
        pcm = np.frombuffer(pcm, dtype=np.int16)
    data = np.asarray(pcm)
    if data.dtype == np.int16:
        return (data.astype(np.float32) / 32768.0).reshape(-1)
    return data.astype(np.float32).reshape(-1)

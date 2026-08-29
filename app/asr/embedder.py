"""Голос в вектор: отпечаток говорящего.

Зачем. Дорожек у нас две, «я» и «они», но за компьютером может сидеть
несколько человек, а в звонке говорят по очереди несколько участников.
Дорожка отвечает на вопрос «откуда пришёл звук», а не «кто говорит».
Здесь мы получаем ответ на второй вопрос: каждая фраза превращается в
вектор, и близкие векторы означают один и тот же голос.

Модель. WeSpeaker ResNet34, обученный на VoxCeleb, 26 МБ в ONNX, работает
на процессоре за миллисекунды. Он выдаёт 256 чисел на фразу; сравнивать
их надо косинусной близостью, а не разностью.

Честное ограничение. Если двое говорят одновременно, их голоса уже
смешаны в одном сигнале, и никакой эмбеддер их не разделит. Это предел
записи, а не модели: на перекрытиях вектор получается смазанным.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

MODEL_REPO = "Wespeaker/wespeaker-voxceleb-resnet34-LM"
MODEL_FILE = "voxceleb_resnet34_LM.onnx"
MODEL_TOTAL_BYTES = 26_500_000

SAMPLE_RATE = 16000
# Короче этого куска отпечаток получается случайным: слишком мало звука,
# чтобы отличить голос от голоса. Такие фразы оставляем без говорящего.
MIN_SECONDS = 0.8

# Параметры фильтр-банка. Их задаёт модель, менять нельзя.
N_MELS = 80
FRAME_LENGTH = 400   # 25 мс при 16 кГц
FRAME_SHIFT = 160    # 10 мс
N_FFT = 512


class EmbedderMissing(RuntimeError):
    """Весов нет на диске. Не ошибка, а повод предложить скачивание."""


class VoiceEmbedder:
    """Считает отпечаток голоса для куска речи."""

    name = "wespeaker-resnet34"
    dim = 256

    def __init__(self, model_dir: Path, auto_load: bool = True) -> None:
        self.model_dir = Path(model_dir)
        self.auto_load = auto_load
        self._session: Any = None
        self._input_name: str = ""
        # Фразы приходят из потока распознавания, а прогрев может
        # случиться из UI: замок не даёт поднять модель дважды.
        self._lock = threading.Lock()
        self._load_failed: str | None = None
        self._mel_basis: np.ndarray | None = None

    @property
    def model_path(self) -> Path:
        return self.model_dir / MODEL_FILE

    def is_downloaded(self) -> bool:
        return self.model_path.exists()

    @property
    def is_loaded(self) -> bool:
        return self._session is not None

    def load(self) -> None:
        """Поднять модель в память. Идемпотентно."""
        with self._lock:
            if self._session is not None:
                return
            if not self.is_downloaded():
                raise EmbedderMissing(f"Нет файла {MODEL_FILE} в {self.model_dir}")
            import onnxruntime as ort

            log.info("Загружаем эмбеддер голоса из %s", self.model_path)
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._session = ort.InferenceSession(
                str(self.model_path), opts, providers=["CPUExecutionProvider"]
            )
            self._input_name = self._session.get_inputs()[0].name
            log.info("Эмбеддер голоса загружен")

    def unload(self) -> None:
        with self._lock:
            self._session = None

    def embed(self, pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray | None:
        """Отпечаток голоса или None, если посчитать нечего.

        None это нормальный ответ, а не ошибка: слишком короткая фраза,
        не скачанная модель или сбой распознавания не должны ронять запись.
        """
        if self._session is None:
            if not self.auto_load or self._load_failed:
                return None
            try:
                self.load()
            except Exception as exc:
                self._load_failed = str(exc)
                log.error("Эмбеддер не загрузился: %s", exc)
                return None

        wave = _to_float32(pcm)
        if wave.size < int(MIN_SECONDS * sample_rate):
            return None

        try:
            feats = self._features(wave, sample_rate)
            if feats.shape[0] < 10:
                return None
            out = self._session.run(None, {self._input_name: feats[None, :, :]})
            vec = np.asarray(out[-1], dtype=np.float32).reshape(-1)
        except Exception:
            log.exception("Не удалось посчитать отпечаток голоса")
            return None

        norm = float(np.linalg.norm(vec))
        if norm < 1e-6:
            return None
        # Нормируем сразу: тогда косинусная близость это просто скалярное
        # произведение, и хранить в базе можно без оглядки на громкость.
        return vec / norm

    # --- признаки --------------------------------------------------------

    def _features(self, wave: np.ndarray, sample_rate: int) -> np.ndarray:
        """Логарифмический мел-фильтр-банк, как ждёт WeSpeaker.

        Считаем сами на numpy, чтобы не тащить torchaudio ради одной
        функции: это лишние сотни мегабайт в будущем бинарнике.
        """
        if sample_rate != SAMPLE_RATE:
            wave = _resample(wave, sample_rate, SAMPLE_RATE)

        # Предыскажение поднимает высокие частоты, где больше всего
        # индивидуальности голоса.
        emphasized = np.append(wave[0], wave[1:] - 0.97 * wave[:-1])

        n_frames = 1 + (len(emphasized) - FRAME_LENGTH) // FRAME_SHIFT
        if n_frames < 1:
            return np.zeros((0, N_MELS), dtype=np.float32)

        idx = (np.arange(FRAME_LENGTH)[None, :]
               + FRAME_SHIFT * np.arange(n_frames)[:, None])
        frames = emphasized[idx] * np.hamming(FRAME_LENGTH)

        spectrum = np.abs(np.fft.rfft(frames, N_FFT)) ** 2
        if self._mel_basis is None:
            self._mel_basis = _mel_basis(SAMPLE_RATE, N_FFT, N_MELS)
        mel = spectrum @ self._mel_basis.T
        feats = np.log(np.maximum(mel, 1e-10)).astype(np.float32)
        # Вычитание среднего по фразе убирает разницу микрофонов и
        # громкости: остаётся то, что зависит от самого голоса.
        return feats - feats.mean(axis=0, keepdims=True)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Близость двух отпечатков: 1 это тот же голос, 0 совсем разные."""
    if a is None or b is None:
        return 0.0
    a = np.asarray(a, dtype=np.float32).reshape(-1)
    b = np.asarray(b, dtype=np.float32).reshape(-1)
    if a.size != b.size or a.size == 0:
        return 0.0
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na < 1e-6 or nb < 1e-6:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _hz_to_mel(hz: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(hz) / 700.0)


def _mel_to_hz(mel: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (10.0 ** (np.asarray(mel) / 2595.0) - 1.0)


def _mel_basis(sample_rate: int, n_fft: int, n_mels: int) -> np.ndarray:
    """Треугольные фильтры, равномерные по шкале мел."""
    low, high = 20.0, sample_rate / 2.0
    points = _mel_to_hz(np.linspace(_hz_to_mel(low), _hz_to_mel(high), n_mels + 2))
    bins = np.floor((n_fft + 1) * points / sample_rate).astype(int)
    basis = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for m in range(1, n_mels + 1):
        left, centre, right = bins[m - 1], bins[m], bins[m + 1]
        # Соседние точки могут совпасть на низких частотах: пропускаем,
        # иначе получится деление на ноль.
        if centre > left:
            basis[m - 1, left:centre] = (np.arange(left, centre) - left) / (centre - left)
        if right > centre:
            basis[m - 1, centre:right] = (right - np.arange(centre, right)) / (right - centre)
    return basis


def _resample(wave: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Простая линейная передискретизация. Захват и так даёт 16 кГц."""
    if src == dst or wave.size == 0:
        return wave
    n = int(round(wave.size * dst / src))
    return np.interp(
        np.linspace(0, wave.size - 1, n, dtype=np.float32),
        np.arange(wave.size, dtype=np.float32),
        wave,
    ).astype(np.float32)


def _to_float32(pcm: np.ndarray | bytes) -> np.ndarray:
    if isinstance(pcm, (bytes, bytearray)):
        pcm = np.frombuffer(pcm, dtype=np.int16)
    data = np.asarray(pcm)
    if data.dtype == np.int16:
        return (data.astype(np.float32) / 32768.0).reshape(-1)
    return data.astype(np.float32).reshape(-1)

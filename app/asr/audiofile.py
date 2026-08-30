"""Чтение звука из файлов любого формата.

Зачем. Встречу не всегда пишет сам Konspekt: часто на руках уже есть
запись из Zoom, диктофона или мессенджера. Такой файл надо уметь
прочитать и распознать так же, как живой звук с микрофона.

Расширению не верим. Это не паранойя, а наблюдение: в присланной пачке
файл `music_1763907667.txt` оказался нормальным MP3, а `0aba19b7f3d1.mp3`
на деле был текстом с расшифровкой. Мессенджеры и выгрузки регулярно
теряют или подменяют расширение, поэтому тип определяет декодер по
содержимому, а имя файла идёт только в заголовок встречи.

Про два канала. Записи звонков часто разложены по каналам: слева один
собеседник, справа другой. Сводить такое в моно — терять готовое, самое
надёжное разделение говорящих, какое вообще бывает: два человека в
разных каналах не смешиваются даже когда перебивают друг друга. Поэтому
стерео мы сначала слушаем: если каналы похожи (обычная музыка, запись с
одного микрофона), сводим в моно; если в них разные голоса, ведём их как
две дорожки, ровно как микрофон и системный звук на живой встрече.

Почему PyAV, а не ffmpeg рядом. Нам нужен один самодостаточный бинарник
без внешних программ в PATH: PyAV везёт ffmpeg внутри колеса и не
требует от человека ничего устанавливать.

Звук приводим к тому, что ждут модели: 16 кГц, float32.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..core.models import Speaker
from .base import SAMPLE_RATE

log = logging.getLogger(__name__)

# Куски, которыми звук отдаётся дальше. Ровно тот же темп, что у живого
# захвата: очередь и VAD рассчитаны на него, а держать в памяти часовую
# запись целиком незачем.
CHUNK_SECONDS = 30.0

# Дальше этого не читаем. Четыре часа это уже не встреча, а подозрительно
# большой файл: лучше честно отказать, чем сутки молотить процессором.
MAX_DURATION = 4 * 60 * 60

# --- когда считать каналы разными людьми ---------------------------------
# Слушаем начало записи и решаем один раз на файл. Пороги подобраны с
# запасом в сторону сведения: ошибочно свести стерео в моно не страшно
# (получится как раньше), а ошибочно разделить обычную музыку на двух
# «собеседников» — это мусор в транскрипте.
PROBE_SECONDS = 90.0      # сколько слушаем, прежде чем решить
SPLIT_CORR = 0.5          # выше этой похожести каналов — точно одно и то же
SPLIT_EXCLUSIVE = 0.15    # доля времени, где говорит ровно один канал
QUIET_CHANNEL = 0.02      # тише этого канал считаем пустым (RMS)


class UnsupportedAudio(RuntimeError):
    """В файле нет звука, который мы умеем прочитать."""


@dataclass
class AudioInfo:
    """Что за файл нам дали."""

    path: Path
    duration: float          # секунды, 0 если контейнер не сказал
    codec: str
    sample_rate: int
    channels: int
    stereo_split: bool = False   # каналы это разные говорящие

    @property
    def title(self) -> str:
        """Имя файла без расширения: из него делаем заголовок встречи."""
        return self.path.stem or self.path.name

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "name": self.path.name,
            "title": self.title,
            "duration": round(self.duration, 1),
            "codec": self.codec,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "stereo_split": self.stereo_split,
        }


def _import_av():
    """PyAV подгружаем лениво: он тяжёлый, а нужен только при импорте файлов."""
    try:
        import av  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover
        raise UnsupportedAudio(
            "Не установлена библиотека для чтения аудиофайлов (av)"
        ) from exc
    return av


def probe(path: str | Path, listen: bool = True) -> AudioInfo:
    """Узнать, есть ли в файле звук, не читая его целиком.

    Отдельный быстрый проход нужен интерфейсу: когда человек бросает в
    окно пачку файлов, надо сразу сказать, какие из них не звук, а не
    выяснять это через минуту молчаливого разбора. Заодно решаем судьбу
    стерео, пока файл всё равно открыт.
    """
    av = _import_av()
    p = Path(path)
    if not p.exists():
        raise UnsupportedAudio(f"Файл не найден: {p.name}")
    if p.is_dir():
        raise UnsupportedAudio(f"Это папка, а не файл: {p.name}")
    if p.stat().st_size == 0:
        raise UnsupportedAudio(f"Файл пуст: {p.name}")

    try:
        with av.open(str(p)) as container:
            streams = [s for s in container.streams if s.type == "audio"]
            if not streams:
                kinds = sorted({s.type for s in container.streams})
                what = ", ".join(kinds) if kinds else "ничего"
                raise UnsupportedAudio(f"В файле нет звуковой дорожки (внутри {what})")
            stream = streams[0]
            duration = 0.0
            if container.duration:
                duration = float(container.duration) / 1_000_000
            elif stream.duration and stream.time_base:
                duration = float(stream.duration * stream.time_base)
            channels = int(getattr(stream.codec_context, "channels", 0) or 0)
            info = AudioInfo(
                path=p,
                duration=max(0.0, duration),
                codec=stream.codec_context.name or "?",
                sample_rate=int(stream.rate or 0),
                channels=channels,
            )
    except UnsupportedAudio:
        raise
    except Exception as exc:
        # Сюда попадают текстовые файлы с расширением .mp3, битые
        # загрузки и всё прочее, что декодер не смог опознать.
        raise UnsupportedAudio(f"Не удалось прочитать как аудио: {_reason(exc)}") from exc

    if listen and channels >= 2:
        try:
            info.stereo_split = _channels_are_different(p)
        except Exception:
            # Не смогли решить — сведём в моно, это всегда безопасно.
            log.debug("Не удалось оценить каналы файла %s", p.name, exc_info=True)
    return info


def _reason(exc: Exception) -> str:
    """Короткое человеческое объяснение вместо кода ошибки ffmpeg."""
    text = str(exc)
    if "Invalid data found" in text:
        return "формат не распознан"
    if "moov atom not found" in text:
        return "файл повреждён или скачан не полностью"
    return text.split(":")[-1].strip()[:80] or type(exc).__name__


def _channels_are_different(path: Path) -> bool:
    """Слева и справа разные люди или одно и то же?

    Смотрим на два признака. Первый — похожесть каналов: у обычного
    стерео она почти единица, потому что это один и тот же звук с чуть
    разной панорамой. Второй, и главный, — сколько времени говорит ровно
    один канал: в диалоге собеседники молчат по очереди, и таких участков
    много, а в музыке их не бывает вовсе.
    """
    left, right = _read_two_channels(path, PROBE_SECONDS)
    if left is None or right is None or left.size < SAMPLE_RATE:
        return False

    rms_l = float(np.sqrt(np.mean(left**2)))
    rms_r = float(np.sqrt(np.mean(right**2)))
    # Один канал пустой: это моно, записанное как стерео. Разделять
    # нечего, вторая «дорожка» дала бы пустого собеседника.
    if min(rms_l, rms_r) < QUIET_CHANNEL:
        log.info("Один канал почти пуст (%.4f и %.4f), сводим в моно", rms_l, rms_r)
        return False

    denom = float(np.std(left) * np.std(right))
    corr = 1.0
    if denom > 1e-9:
        corr = float(np.mean((left - left.mean()) * (right - right.mean())) / denom)
    if corr > SPLIT_CORR:
        log.info("Каналы похожи (corr=%.2f), сводим в моно", corr)
        return False

    exclusive = _exclusive_talk_ratio(left, right)
    split = exclusive >= SPLIT_EXCLUSIVE
    log.info(
        "Каналы: corr=%.2f, поочерёдная речь %.0f%% — %s",
        corr, exclusive * 100, "ведём как две дорожки" if split else "сводим в моно",
    )
    return split


def _exclusive_talk_ratio(left: np.ndarray, right: np.ndarray) -> float:
    """Доля времени, когда звучит ровно один канал из двух.

    Признак диалога, а не стерео: в записи звонка собеседники говорят по
    очереди, поэтому таких участков много. В музыке или в стереозаписи с
    одного микрофона оба канала звучат всегда вместе.
    """
    frame = int(SAMPLE_RATE * 0.05)  # 50 мс
    count = min(len(left), len(right)) // frame
    if count < 20:
        return 0.0
    el = np.sqrt(np.mean(left[: count * frame].reshape(count, frame) ** 2, axis=1))
    er = np.sqrt(np.mean(right[: count * frame].reshape(count, frame) ** 2, axis=1))
    # Порог по каждому каналу свой: громкость собеседников редко совпадает.
    tl = max(float(np.percentile(el, 60)), QUIET_CHANNEL)
    tr = max(float(np.percentile(er, 60)), QUIET_CHANNEL)
    loud_l, loud_r = el > tl, er > tr
    only_one = np.logical_xor(loud_l, loud_r)
    anyone = np.logical_or(loud_l, loud_r)
    if not anyone.any():
        return 0.0
    return float(only_one.sum() / anyone.sum())


def _read_two_channels(path: Path, seconds: float) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Прочитать начало файла двумя отдельными каналами."""
    av = _import_av()
    limit = int(SAMPLE_RATE * seconds)
    left: list[np.ndarray] = []
    right: list[np.ndarray] = []
    got = 0
    with av.open(str(path)) as container:
        streams = [s for s in container.streams if s.type == "audio"]
        if not streams:
            return None, None
        stream = streams[0]
        # fltp = planar: каналы приходят отдельными строками. В packed
        # формате они чередуются в одном ряду, и разделить их сложнее.
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=SAMPLE_RATE)
        for frame in container.decode(stream):
            for piece in resampler.resample(frame):
                arr = piece.to_ndarray()
                if arr.ndim != 2 or arr.shape[0] < 2:
                    continue
                left.append(arr[0])
                right.append(arr[1])
                got += arr.shape[1]
            if got >= limit:
                break
    if not left:
        return None, None
    return (np.concatenate(left).astype(np.float32),
            np.concatenate(right).astype(np.float32))


def decode(
    path: str | Path,
    chunk_seconds: float = CHUNK_SECONDS,
    split_channels: bool = False,
):
    """Читать файл кусками: (дорожка, звук, смещение в секундах).

    Отдаём генератором, а не одним массивом: часовая запись во float32
    это сотни мегабайт, и держать её целиком незачем, раз распознавание
    всё равно идёт по кускам.

    При split_channels левый канал идёт дорожкой «я», правый —
    «собеседник». Это соглашение записей звонков: своя сторона слева.
    """
    av = _import_av()
    p = Path(path)
    target = int(SAMPLE_RATE * chunk_seconds)
    tracks = (Speaker.ME.value, Speaker.THEM.value) if split_channels else (Speaker.THEM.value,)
    buffers: dict[str, list[np.ndarray]] = {t: [] for t in tracks}
    filled = dict.fromkeys(tracks, 0)
    position = 0.0

    with av.open(str(p)) as container:
        streams = [s for s in container.streams if s.type == "audio"]
        if not streams:
            raise UnsupportedAudio("В файле нет звуковой дорожки")
        stream = streams[0]
        # Декодируем только звук: у видеофайла кадры картинки нам не нужны
        # и стоят дорого.
        stream.thread_type = "AUTO"
        if split_channels:
            resampler = av.AudioResampler(format="fltp", layout="stereo", rate=SAMPLE_RATE)
        else:
            resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)

        def flush_ready():
            """Отдать все куски, набравшие полный размер."""
            nonlocal position
            while all(filled[t] >= target for t in tracks):
                for track in tracks:
                    joined = np.concatenate(buffers[track])
                    yield track, joined[:target], position
                    rest = joined[target:]
                    buffers[track] = [rest] if rest.size else []
                    filled[track] = rest.size
                position += target / SAMPLE_RATE

        for frame in container.decode(stream):
            for piece in resampler.resample(frame):
                arr = piece.to_ndarray()
                if split_channels:
                    if arr.ndim != 2 or arr.shape[0] < 2:
                        continue
                    chunks = {tracks[0]: arr[0], tracks[1]: arr[1]}
                else:
                    chunks = {tracks[0]: _flatten(arr)}
                for track, samples in chunks.items():
                    if samples.size == 0:
                        continue
                    buffers[track].append(np.ascontiguousarray(samples, dtype=np.float32))
                    filled[track] += samples.size
                yield from flush_ready()
            if position > MAX_DURATION:
                log.warning("Файл длиннее %d ч, обрываем чтение", MAX_DURATION // 3600)
                return

        # Хвост короче куска: его тоже надо отдать, иначе потеряем
        # последние секунды записи вместе с последней фразой.
        for piece in resampler.resample(None) or []:
            arr = piece.to_ndarray()
            if split_channels:
                if arr.ndim == 2 and arr.shape[0] >= 2:
                    for track, samples in ((tracks[0], arr[0]), (tracks[1], arr[1])):
                        buffers[track].append(np.ascontiguousarray(samples, dtype=np.float32))
                        filled[track] += samples.size
            else:
                samples = _flatten(arr)
                if samples.size:
                    buffers[tracks[0]].append(samples)
                    filled[tracks[0]] += samples.size
        yield from flush_ready()
        for track in tracks:
            if filled[track]:
                yield track, np.concatenate(buffers[track]), position


def _flatten(arr: np.ndarray) -> np.ndarray:
    """Кадр PyAV в одномерный float32.

    Ресемплер уже свёл звук в моно, но форма массива зависит от того,
    planar формат или нет, поэтому приводим её здесь.
    """
    if arr.ndim > 1:
        arr = arr.mean(axis=0) if arr.shape[0] < arr.shape[1] else arr.mean(axis=1)
    return np.ascontiguousarray(arr, dtype=np.float32).reshape(-1)

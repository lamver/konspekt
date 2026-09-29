"""Смысл фразы в числах: модель для поиска по смыслу.

Зачем. Поиск по словам находит «платежи», только если человек написал
«платеж». А помнит он обычно не слово, а тему: «где мы говорили про
оплату картой». Модель превращает кусок текста в 384 числа так, что
близкие по смыслу куски получают близкие числа, даже если слова в них
разные: «тестовые платежи картой» находит «нельзя проводить тестовые
платежи без верификации».

Модель. multilingual-e5-small от Microsoft (лицензия MIT), сжатая до
восьмибитной версии в ONNX: 118 МБ весов и 17 МБ словаря. Выбрана не
наугад, а на настоящем архиве в 109 встреч против rubert-tiny-turbo:
та поднимала наверх пустые обрывки вроде «а.» и «Д», эта нет.

Тонкость, без которой всё работает, но хуже. Модель учили с пометками
«query: » перед вопросом и «passage: » перед текстом, где ищут. Без них
она сравнивает вопрос с текстом как текст с текстом, и короткий запрос
начинает тянуться к коротким обрывкам.

Честное ограничение. Общие запросы вроде «когда релиз» модель понимает
плохо и отвечает чем попало, поэтому поиск по словам остаётся главным,
а смысл лишь добавляет то, что словами не нашлось.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

MODEL_REPO = "Xenova/multilingual-e5-small"
MODEL_FILE = "onnx/model_quantized.onnx"
TOKENIZER_FILE = "tokenizer.json"
MODEL_FILES = (MODEL_FILE, TOKENIZER_FILE)
# Каталог весов внутри models_dir().
MODEL_DIR_NAME = "e5-small"
MODEL_TOTAL_BYTES = 118_308_185 + 17_082_730
# Имя модели записывается рядом с векторами. Сменим модель — старые
# векторы станут несравнимы с новыми, и указатель должен это понять
# сам, а не выдавать случайные совпадения.
MODEL_NAME = "multilingual-e5-small-int8"
DIM = 384

# Длиннее модель всё равно не читает, а реплики у нас по 50 знаков, окна
# по 300: в 256 кусочков слов это помещается с запасом.
MAX_TOKENS = 256
BATCH = 32
# Два потока, а не все ядра: указатель пересчитывается фоном, пока
# человек работает, и отнимать у него процессор незачем.
THREADS = 2

QUERY = "query: "
PASSAGE = "passage: "


class MeaningMissing(RuntimeError):
    """Весов нет на диске. Не ошибка, а повод их скачать."""


class MeaningModel:
    """Считает векторы смысла для запросов и кусков расшифровки."""

    name = MODEL_NAME
    dim = DIM

    def __init__(self, model_dir: Path) -> None:
        self.model_dir = Path(model_dir)
        self._session: Any = None
        self._tokenizer: Any = None
        self._inputs: list[str] = []
        # Запрос из окна и фоновый пересчёт могут прийти одновременно.
        self._lock = threading.Lock()

    def is_downloaded(self) -> bool:
        return all((self.model_dir / f).exists() for f in MODEL_FILES)

    @property
    def is_loaded(self) -> bool:
        return self._session is not None

    def load(self) -> None:
        """Поднять модель в память. Повторный вызов ничего не делает."""
        with self._lock:
            if self._session is not None:
                return
            if not self.is_downloaded():
                raise MeaningMissing(f"Нет файлов {MODEL_FILES} в {self.model_dir}")
            import onnxruntime as ort
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(self.model_dir / TOKENIZER_FILE))
            tokenizer.enable_truncation(MAX_TOKENS)
            tokenizer.enable_padding()

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = THREADS
            opts.inter_op_num_threads = 1
            session = ort.InferenceSession(
                str(self.model_dir / MODEL_FILE), opts,
                providers=["CPUExecutionProvider"],
            )
            self._inputs = [i.name for i in session.get_inputs()]
            self._tokenizer = tokenizer
            self._session = session
            log.info("Модель смысла загружена из %s", self.model_dir)

    def unload(self) -> None:
        with self._lock:
            self._session = None
            self._tokenizer = None

    def query(self, text: str) -> np.ndarray:
        """Вектор поискового запроса."""
        return self._embed([text], QUERY)[0]

    def passages(self, texts: list[str]) -> np.ndarray:
        """Векторы кусков расшифровки, по строке на кусок."""
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        return self._embed(texts, PASSAGE)

    def _embed(self, texts: list[str], prefix: str) -> np.ndarray:
        self.load()
        out = []
        for i in range(0, len(texts), BATCH):
            with self._lock:
                encoded = self._tokenizer.encode_batch(
                    [prefix + t for t in texts[i:i + BATCH]]
                )
                ids = np.array([e.ids for e in encoded], dtype=np.int64)
                mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
                feed = {"input_ids": ids, "attention_mask": mask}
                if "token_type_ids" in self._inputs:
                    feed["token_type_ids"] = np.zeros_like(ids)
                hidden = self._session.run(None, feed)[0]
            out.append(mean_pool(hidden, mask))
        return np.vstack(out)


def mean_pool(hidden: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Среднее по настоящим кусочкам слов, нормированное к длине 1.

    Среднее, а не первый кусочек: так модель учили. Заполнитель, которым
    короткие фразы добиваются до длины длинных, в среднее не входит,
    иначе одна и та же фраза давала бы разный смысл в зависимости от
    соседей по пачке.
    """
    m = mask[..., None].astype(np.float32)
    summed = (hidden * m).sum(axis=1)
    counts = np.maximum(m.sum(axis=1), 1e-9)
    vectors = summed / counts
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / np.maximum(norms, 1e-12)).astype(np.float32)

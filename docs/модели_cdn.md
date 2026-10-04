# Веса моделей для своего CDN

Откуда Konspekt качает модели и как их разложить у себя, чтобы программа
не зависела от Hugging Face. Список собран `tools/зеркало_моделей.py`
из API Hugging Face, ссылки закреплены на ревизию: файл по такой ссылке
не поменяется, даже если автор перезальёт модель.

## Как разложить

Путь на CDN повторяет путь на Hugging Face без `resolve/<ревизия>`:

```
<основа>/<владелец>/<репозиторий>/<файл>
```

Например `https://cdn.example.ru/konspekt/models/istupakov/gigaam-v3-onnx/v3_e2e_rnnt_encoder.int8.onnx`.
Так загрузчику не нужна отдельная таблица путей: он подставляет другую
основу к тем же репозиторию и файлу. Имена с подкаталогом, как
`onnx/encoder_model_int8.onnx`, кладутся с подкаталогом.

Сервер должен отдавать `Content-Length` и поддерживать `Range`: на слабом
канале загрузка идёт кусками с докачкой, без `Range` каждый обрыв
начинает файл заново.

Скачать всё разом в такую раскладку:

```
python tools/зеркало_моделей.py скачать D:\konspekt-cdn
```

Скрипт сверяет sha256 каждого файла, докачивает оборванное и кладёт
рядом `manifest.json`. Его тоже стоит выложить: по нему можно проверить,
что на CDN лежат ровно эти файлы.

Всего 10.2 ГБ, из них модели для заметок: быстрая 1,8 ГБ, умная 2,5 ГБ,
мощная 5,0 ГБ. Мощную можно не выкладывать, пока её нет в программе.

## Почему не в архивах

Замер 30.09 на тех же файлах, кусок по 32 МБ:

| Файл | zip | xz |
|---|---:|---:|
| Qwen3-1.7B-Q8_0.gguf | 3,7% | 3,1% |
| Qwen3-4B-Q4_K_M.gguf | 2,0% | 2,5% |
| GigaAM, кодировщик int8 | 26,4% | 27,6% |
| e5-small, int8 | 33,1% | 35,8% |
| WeSpeaker | 7,0% | 7,4% |

Модели для заметок это 9 из 10 ГБ, и они почти не жмутся: квантованные
числа уже плотные. ONNX жмётся на треть, но весит всего полгигабайта.
Итого выигрыш около 3%, а цена такая: докачка по `Range` работает
только для целого файла, а распаковка на компьютере человека — ещё одно
место, где можно упасть без места на диске. Сжатие на лету (gzip на
CDN) тоже не нужно: программа его не просит, и файлы уйдут как есть.

## Список

### Распознавание русского (GigaAM v3)

Репозиторий [`istupakov/gigaam-v3-onnx`](https://huggingface.co/istupakov/gigaam-v3-onnx), ревизия `322c3b29492673eb7d0b434bfa9dfb8653e34d02`,
226.4 МБ.

| Файл | Размер | sha256 |
|---|---:|---|
| [v3_e2e_rnnt_encoder.int8.onnx](https://huggingface.co/istupakov/gigaam-v3-onnx/resolve/322c3b29492673eb7d0b434bfa9dfb8653e34d02/v3_e2e_rnnt_encoder.int8.onnx) | 224 570 477 | `4e0e076a6076cd110277e529b8ac8f32cd5297f7fbebad5341ae8ddb7d00817b` |
| [v3_e2e_rnnt_decoder.int8.onnx](https://huggingface.co/istupakov/gigaam-v3-onnx/resolve/322c3b29492673eb7d0b434bfa9dfb8653e34d02/v3_e2e_rnnt_decoder.int8.onnx) | 1 159 170 | `89014e134865615b91e037157e46e389b1271e6072460efc010ea08e61e23146` |
| [v3_e2e_rnnt_joint.int8.onnx](https://huggingface.co/istupakov/gigaam-v3-onnx/resolve/322c3b29492673eb7d0b434bfa9dfb8653e34d02/v3_e2e_rnnt_joint.int8.onnx) | 687 791 | `ade116563dbf66e503b0994efab6b5861412743e52bf31c39fc3fffa3783d5d1` |
| [v3_e2e_rnnt.yaml](https://huggingface.co/istupakov/gigaam-v3-onnx/resolve/322c3b29492673eb7d0b434bfa9dfb8653e34d02/v3_e2e_rnnt.yaml) | 1 041 | `(мелкий файл  не LFS)` |
| [v3_e2e_rnnt_vocab.txt](https://huggingface.co/istupakov/gigaam-v3-onnx/resolve/322c3b29492673eb7d0b434bfa9dfb8653e34d02/v3_e2e_rnnt_vocab.txt) | 13 354 | `(мелкий файл  не LFS)` |
| [config.json](https://huggingface.co/istupakov/gigaam-v3-onnx/resolve/322c3b29492673eb7d0b434bfa9dfb8653e34d02/config.json) | 135 | `(мелкий файл  не LFS)` |

### Распознавание других языков (Whisper small)

Репозиторий [`onnx-community/whisper-small`](https://huggingface.co/onnx-community/whisper-small), ревизия `36050c46d777d46dc4b5f43f6d90574fc38f8732`,
251.0 МБ.

| Файл | Размер | sha256 |
|---|---:|---|
| [onnx/encoder_model_int8.onnx](https://huggingface.co/onnx-community/whisper-small/resolve/36050c46d777d46dc4b5f43f6d90574fc38f8732/onnx/encoder_model_int8.onnx) | 92 326 127 | `2601c9eb2d345c5916d4576d36f663a7c96589740fb2273828c48c3fc2c7db75` |
| [onnx/decoder_model_merged_int8.onnx](https://huggingface.co/onnx-community/whisper-small/resolve/36050c46d777d46dc4b5f43f6d90574fc38f8732/onnx/decoder_model_merged_int8.onnx) | 156 750 845 | `ec07c3cbb64172c39791e26ee870a65ac22b458c36722bfe2776b3dbf741e0c9` |
| [vocab.json](https://huggingface.co/onnx-community/whisper-small/resolve/36050c46d777d46dc4b5f43f6d90574fc38f8732/vocab.json) | 1 036 584 | `(мелкий файл  не LFS)` |
| [added_tokens.json](https://huggingface.co/onnx-community/whisper-small/resolve/36050c46d777d46dc4b5f43f6d90574fc38f8732/added_tokens.json) | 34 604 | `(мелкий файл  не LFS)` |
| [config.json](https://huggingface.co/onnx-community/whisper-small/resolve/36050c46d777d46dc4b5f43f6d90574fc38f8732/config.json) | 2 227 | `(мелкий файл  не LFS)` |
| [generation_config.json](https://huggingface.co/onnx-community/whisper-small/resolve/36050c46d777d46dc4b5f43f6d90574fc38f8732/generation_config.json) | 3 893 | `(мелкий файл  не LFS)` |
| [preprocessor_config.json](https://huggingface.co/onnx-community/whisper-small/resolve/36050c46d777d46dc4b5f43f6d90574fc38f8732/preprocessor_config.json) | 339 | `(мелкий файл  не LFS)` |
| [tokenizer_config.json](https://huggingface.co/onnx-community/whisper-small/resolve/36050c46d777d46dc4b5f43f6d90574fc38f8732/tokenizer_config.json) | 282 683 | `(мелкий файл  не LFS)` |
| [special_tokens_map.json](https://huggingface.co/onnx-community/whisper-small/resolve/36050c46d777d46dc4b5f43f6d90574fc38f8732/special_tokens_map.json) | 2 194 | `(мелкий файл  не LFS)` |
| [normalizer.json](https://huggingface.co/onnx-community/whisper-small/resolve/36050c46d777d46dc4b5f43f6d90574fc38f8732/normalizer.json) | 52 666 | `(мелкий файл  не LFS)` |
| [merges.txt](https://huggingface.co/onnx-community/whisper-small/resolve/36050c46d777d46dc4b5f43f6d90574fc38f8732/merges.txt) | 493 869 | `(мелкий файл  не LFS)` |

### Распознавание других языков (Whisper base)

Репозиторий [`onnx-community/whisper-base`](https://huggingface.co/onnx-community/whisper-base), ревизия `1846881b6b3a3024392c1eea3ad983695bc23925`,
78.8 МБ.

| Файл | Размер | sha256 |
|---|---:|---|
| [onnx/encoder_model_int8.onnx](https://huggingface.co/onnx-community/whisper-base/resolve/1846881b6b3a3024392c1eea3ad983695bc23925/onnx/encoder_model_int8.onnx) | 23 201 297 | `ca6177401f86a2c6b4dc5f7fc02fbca680678906bd0c22f6d89f0b80f124253f` |
| [onnx/decoder_model_merged_int8.onnx](https://huggingface.co/onnx-community/whisper-base/resolve/1846881b6b3a3024392c1eea3ad983695bc23925/onnx/decoder_model_merged_int8.onnx) | 53 693 315 | `fa3ef9902734ce5ae6f9ef2bdb2ba9a6c4b5785b09f4f420ce036573dc9d090b` |
| [vocab.json](https://huggingface.co/onnx-community/whisper-base/resolve/1846881b6b3a3024392c1eea3ad983695bc23925/vocab.json) | 1 036 584 | `(мелкий файл  не LFS)` |
| [added_tokens.json](https://huggingface.co/onnx-community/whisper-base/resolve/1846881b6b3a3024392c1eea3ad983695bc23925/added_tokens.json) | 34 604 | `(мелкий файл  не LFS)` |
| [config.json](https://huggingface.co/onnx-community/whisper-base/resolve/1846881b6b3a3024392c1eea3ad983695bc23925/config.json) | 2 243 | `(мелкий файл  не LFS)` |
| [generation_config.json](https://huggingface.co/onnx-community/whisper-base/resolve/1846881b6b3a3024392c1eea3ad983695bc23925/generation_config.json) | 3 832 | `(мелкий файл  не LFS)` |
| [preprocessor_config.json](https://huggingface.co/onnx-community/whisper-base/resolve/1846881b6b3a3024392c1eea3ad983695bc23925/preprocessor_config.json) | 339 | `(мелкий файл  не LFS)` |
| [tokenizer_config.json](https://huggingface.co/onnx-community/whisper-base/resolve/1846881b6b3a3024392c1eea3ad983695bc23925/tokenizer_config.json) | 282 682 | `(мелкий файл  не LFS)` |
| [special_tokens_map.json](https://huggingface.co/onnx-community/whisper-base/resolve/1846881b6b3a3024392c1eea3ad983695bc23925/special_tokens_map.json) | 2 194 | `(мелкий файл  не LFS)` |
| [normalizer.json](https://huggingface.co/onnx-community/whisper-base/resolve/1846881b6b3a3024392c1eea3ad983695bc23925/normalizer.json) | 52 666 | `(мелкий файл  не LFS)` |
| [merges.txt](https://huggingface.co/onnx-community/whisper-base/resolve/1846881b6b3a3024392c1eea3ad983695bc23925/merges.txt) | 493 869 | `(мелкий файл  не LFS)` |

### Распознавание сербского (своя выгрузка)

Только на CDN, на Hugging Face этой выгрузки нет: путь
`konspekt/whisper-small-sr/<файл>`. Исходник —
[`Sagicc/whisper-small-sr-yodas-v2`](https://huggingface.co/Sagicc/whisper-small-sr-yodas-v2)
(Apache 2.0), выгружен в ONNX через optimum и сжат в int8 вместе с
ветвями декодера (`EnableSubgraph`), иначе декодер весит 774 МБ вместо
195. Качается только тем, кому нужен сербский. 289.5 МБ.

| Файл | Размер | sha256 |
|---|---:|---|
| onnx/encoder_model_int8.onnx | 92 231 161 | `4ee67529ef776ca924b878ff3000c8a0edc0b842bfbd2d710eb4017f5266ec6c` |
| onnx/decoder_model_merged_int8.onnx | 195 141 611 | `cd1aae47a7a13a572a4b2fe553f3384765d5efb5f641eae61c6c1a4c2b43eeae` |
| vocab.json | 1 086 844 | `40a31d8bd67dd6b8a8c7531b30595557adbadad6578a2f657c60bcffca7feabd` |
| added_tokens.json | 36 213 | `8a5856a00b438f6fc40d2c6d9d93fea54f2c169bdc817a58d1feddb9ac96302e` |
| config.json | 1 489 | `cec82fa5d9223adcb3ddfe126cf94d877513b7e4ac85fe4871cac67e76aff898` |
| generation_config.json | 4 103 | `7d701171561ad9d00158a3d2cce76f1e3ee455fd4e8baa7761c6dfdf6271609f` |
| preprocessor_config.json | 353 | `86c752c0f26ae2cc92699a1805eb66366b01c9260b7d31c7bd142d6b9e56017a` |
| tokenizer_config.json | 295 703 | `f46e9819a8910572da1b528afde146a56c63068e1a130ffc66f7b5315093b0e1` |
| special_tokens_map.json | 2 333 | `aa84bd013194e2bf2450397e49e656d8b8d9e1f971c0f1702409870b2cf14106` |
| normalizer.json | 54 408 | `6c40cc36b4bb9c5aa8be0ff9023ea4e78a3ab718b3490a1d9cd3cb7ec56f130f` |
| merges.txt | 543 870 | `0b4a5df397fd681b15714777a7ce4af868bac7591eccde0f124b628dce9fba64` |

### Определение языка (VoxLingua107)

Репозиторий [`beginning-ai/speechbrain-lang-id-voxlingua107-ecapa-onnx`](https://huggingface.co/beginning-ai/speechbrain-lang-id-voxlingua107-ecapa-onnx), ревизия `746b6a8f7ec7687f637470086a401e77414bf02e`,
85.4 МБ.

| Файл | Размер | sha256 |
|---|---:|---|
| [model.onnx](https://huggingface.co/beginning-ai/speechbrain-lang-id-voxlingua107-ecapa-onnx/resolve/746b6a8f7ec7687f637470086a401e77414bf02e/model.onnx) | 85 408 466 | `f7d7a3856eec4087dc39fbf5df06ce75a978b45db6121f8afb6fad4796d1d2ed` |
| [labels.json](https://huggingface.co/beginning-ai/speechbrain-lang-id-voxlingua107-ecapa-onnx/resolve/746b6a8f7ec7687f637470086a401e77414bf02e/labels.json) | 6 241 | `(мелкий файл  не LFS)` |

### Голоса говорящих (WeSpeaker)

Репозиторий [`Wespeaker/wespeaker-voxceleb-resnet34-LM`](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM), ревизия `f0c48c298fd835726c27956a5d617bad7115627e`,
26.5 МБ.

| Файл | Размер | sha256 |
|---|---:|---|
| [voxceleb_resnet34_LM.onnx](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/f0c48c298fd835726c27956a5d617bad7115627e/voxceleb_resnet34_LM.onnx) | 26 530 309 | `7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068` |

### Поиск по смыслу (multilingual-e5-small)

Репозиторий [`Xenova/multilingual-e5-small`](https://huggingface.co/Xenova/multilingual-e5-small), ревизия `761b726dd34fb83930e26aab4e9ac3899aa1fa78`,
135.4 МБ.

| Файл | Размер | sha256 |
|---|---:|---|
| [onnx/model_quantized.onnx](https://huggingface.co/Xenova/multilingual-e5-small/resolve/761b726dd34fb83930e26aab4e9ac3899aa1fa78/onnx/model_quantized.onnx) | 118 308 185 | `f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193` |
| [tokenizer.json](https://huggingface.co/Xenova/multilingual-e5-small/resolve/761b726dd34fb83930e26aab4e9ac3899aa1fa78/tokenizer.json) | 17 082 730 | `0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39` |

### Заметки и чат, быстрая (Qwen3 1.7B)

Репозиторий [`Qwen/Qwen3-1.7B-GGUF`](https://huggingface.co/Qwen/Qwen3-1.7B-GGUF), ревизия `90862c4b9d2787eaed51d12237eafdfe7c5f6077`,
1 834.4 МБ.

| Файл | Размер | sha256 |
|---|---:|---|
| [Qwen3-1.7B-Q8_0.gguf](https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/resolve/90862c4b9d2787eaed51d12237eafdfe7c5f6077/Qwen3-1.7B-Q8_0.gguf) | 1 834 426 016 | `061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a` |

### Заметки и чат, умная (Qwen3 4B)

Репозиторий [`Qwen/Qwen3-4B-GGUF`](https://huggingface.co/Qwen/Qwen3-4B-GGUF), ревизия `bc640142c66e1fdd12af0bd68f40445458f3869b`,
2 497.3 МБ.

| Файл | Размер | sha256 |
|---|---:|---|
| [Qwen3-4B-Q4_K_M.gguf](https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf) | 2 497 280 256 | `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5` |

### Заметки и чат, мощная (Qwen3 8B)

Репозиторий [`Qwen/Qwen3-8B-GGUF`](https://huggingface.co/Qwen/Qwen3-8B-GGUF), ревизия `7c41481f57cb95916b40956ab2f0b139b296d974`,
5 027.8 МБ.

| Файл | Размер | sha256 |
|---|---:|---|
| [Qwen3-8B-Q4_K_M.gguf](https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/7c41481f57cb95916b40956ab2f0b139b296d974/Qwen3-8B-Q4_K_M.gguf) | 5 027 783 488 | `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785` |

## Не модели

Движок llama.cpp (`llama-server.exe`) едет в установщике и с CDN не
качается. Обновления программы берутся с релизов GitHub
`lamver/konspekt-releases`.

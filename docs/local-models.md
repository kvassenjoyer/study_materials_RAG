# Локальные модели и проверка адаптеров

Default backend использует локальный Ollama на `http://127.0.0.1:11434`,
`embeddinggemma:300m` для embeddings и `qwen3.5:4b` для ответов и переформулирования вопросов.
Никакая модель не подменяется автоматически при ошибке. OpenRouter остаётся этапом финальной
версии, согласуемым отдельно.

## Проверенный runtime

На машине пользователя проверены Apple M3 Pro, arm64, 36 GB unified memory, GPU 18 cores
и Metal. На 2026-10-04 существующий daemon Ollama версии `0.32.14` был доступен через
`/usr/local/bin/ollama`; отдельный server не запускался. Существующие модели не изменялись.

| Компонент | Точный идентификатор | Артефакт / размер |
|---|---|---|
| Embeddings, default | `embeddinggemma:300m` | BF16; ID `85462619ee72`; около 622 MB |
| Ответы и rewrite | `qwen3.5:4b` | Q4_K_M; ID `2a654d98e6fb`; около 3.4 GB |
| Chunk / generation tokenizer | `Qwen/Qwen3.5-4B` | Публичный HF `tokenizer.json`, cache `.data/models` |
| Embeddings, opt-in original HF | `google/embeddinggemma-300m` | Original float32; около 1.2 GB весов; отдельный профиль |

Первичная проверка `/api/ps` показала обе Ollama-модели на GPU: EmbeddingGemma около
673 MB runtime memory при context 2048, Qwen около 3.38 GB при context 8192. Это наблюдение,
а не гарантированный верхний предел: приложение задаёт Qwen context 16384, и память также
зависит от параллелизма и версии runtime. [Ollama поддержка GPU](https://docs.ollama.com/gpu).

Скачивание и повторный запуск:

```sh
/usr/local/bin/ollama pull embeddinggemma:300m
/usr/local/bin/ollama pull qwen3.5:4b
/usr/local/bin/ollama list
```

Если daemon выключен, Ollama запускается обычным `ollama serve`. Приложение не запускает
daemon, не скачивает Ollama-веса и не закрывает общий daemon пользователя.

## Prefixes, размеры и усечение

Оба embedding backend форматируют запрос один раз:

```text
task: search result | query: <question>
title: <filename> | text: <chunk>
```

Из title удаляются разделитель `|` и лишние пробелы, затем title ограничивается 256 UTF-8
bytes на границе символа. Это соответствует назначению query/document prefixes из
[карточки EmbeddingGemma](https://huggingface.co/google/embeddinggemma-300m).

У проверенного Ollama `embeddinggemma:300m` Modelfile содержит `TEMPLATE {{ .Prompt }}`;
`/api/embed` в этой версии передаёт строки непосредственно в tokenize/embed. Prefixes
задаются адаптером вручную. Это проверено исходным кодом
[Ollama EmbedHandler v0.32.14](https://github.com/ollama/ollama/blob/v0.32.14/server/routes.go)
и разным числом input tokens для raw и prefixed запроса. В HF adapter уже форматированные
строки передаются в Sentence Transformers с `prompt=""`, чтобы отключить его default query
prompt и предотвратить повторный prefix.

Default размерность — 768; адаптеры также проверяют поддерживаемые Matryoshka размеры
128/256/512/768. Векторы проверяются на точное число строк, размерность, конечность и
ненулевую норму, затем L2-нормализуются. Для HF прежде чем обрезать размерность, проверяется
исходная размерность 768.

Chunking сохраняет границы PDF-страниц и исходный Unicode-текст. Настройки: максимум 512
content tokens, overlap 64; tail не теряется. UUID чанка детерминирован от document UUID,
embedding profile и ordinal. Default Ollama профиль использует публичный Qwen tokenizer
для chunk/generation budgets; это **не точный tokenizer EmbeddingGemma**. Поэтому дополнительно
проверяются content <=1536 UTF-8 bytes и formatted input <=1900 bytes, а `/api/embed`
вызывается с `truncate:false`. Если runtime отвергает вход, адаптер возвращает ошибку,
без скрытого усечения. [Ollama embed API](https://docs.ollama.com/api/embed).

HF профиль использует собственный EmbeddingGemma tokenizer. Адаптер до encode проверяет
полный formatted input с special tokens против 2048 и вызывает tokenizer с
`truncation=False`. HF и Ollama profiles включают разные backend/tokenizer/budgets;
их индексы не смешиваются. При смене профиля требуется переиндексация.

## Original Hugging Face backend

Google repository публично виден, но файлы gated. Публичная проверка HF metadata вернула
`gated: manual`; unauthenticated HEAD для `tokenizer.json` — HTTP 401. Qwen repository
вернул `gated: false`. Наличие публичной карточки Google не означает доступ к весам.
[Google metadata](https://huggingface.co/api/models/google/embeddinggemma-300m),
[Qwen metadata](https://huggingface.co/api/models/Qwen/Qwen3.5-4B).

Для opt-in HF backend пользователь самостоятельно принимает условия на странице модели
и при необходимости авторизует HF CLI локально. Секреты не передаются в чаты и не выводятся
приложением. Установить optional зависимости и выбрать backend можно так:

```sh
uv sync --extra huggingface
```

Затем установить `STUDY_RAG_EMBEDDING_BACKEND=huggingface`; полный список settings указан
в `.env.example`. Device `auto` выбирает MPS, затем CUDA, затем CPU по доступности PyTorch.
`cpu`, `mps` и `cuda` можно задать явно; недоступный явно выбранный device возвращает
понятную dependency error. Encode сериализован и вынесен из event loop.
Sentence Transformers загружает оригинальную модель с `trust_remote_code=False`,
`torch_dtype=torch.float32`, затем `.float()`. fp16 не используется.

Реальный original HF inference в этой сессии не подтверждён: Google доступ gated, а heavy
extra не устанавливался. Unit tests проверяют его границы через typed encoder double:
prefix один раз, точный token cap до encode, native dimensions, normalization, device
resolution. Default Ollama inference выполнен с реальными скачанными моделями.

## Ответы и история

Ответы используют `/api/chat`, `stream:false`, `think:false`, JSON schema, context 16384,
output 512 и temperature 0.1. История ограничена последними восемью completed сообщениями
и 2048 tokens. История помогает интерпретировать короткий follow-up, но источником фактов
служат только переданные excerpts. При нехватке бюджета сначала удаляются целые старые
сообщения, затем целые последние источники; source labels `S1..S6` сохраняют порядок.

Без evidence адаптер сразу возвращает RU/EN отказ. Invalid JSON, незавершённый/length
response, неизвестный inline label или declared label, а также положительный ответ без
source labels приводят к dependency error и не публикуются как успешный grounded answer.
После проверки model-declared source labels сервер добавляет отсутствующие inline markers
`[Sx]` в конец ответа в порядке объявленных labels; повторные labels не дублируются.
Уже присутствующие markers сохраняются. Это детерминированное форматирование не меняет
фактический текст и не добавляет новых source IDs; дополнительных model requests нет.
Это проверки структуры и ссылок; они не доказывают фактическую корректность всех утверждений
модели. [Ollama chat API](https://docs.ollama.com/api/chat).

HTTP calls сериализованы внутри каждого adapter transport. Ошибки сети, timeout, HTTP
429/500/502/503/504 повторяются максимум три раза с небольшим exponential backoff и jitter;
другие HTTP 4xx не повторяются. Timeout одной попытки <=40 seconds; ожидание очереди и retry
включены в общий operation budget. Root также ограничивает совокупный пользовательский
turn, включая rewrite и answer.

## Фактические проверки

Прямой smoke Ollama API с синтетическими материалами RU/EN подтвердил:

- embedding `embeddinggemma:300m`: 768 finite dimensions, unit norm; 256 dimensions также
  проверены. RU relevant cosine 0.614 против unrelated 0.298; EN 0.591 против 0.388;
- переполнение входа с `truncate:false` возвращает HTTP 400 `input length exceeds context length`;
- Qwen `qwen3.5:4b`, `think:false`, structured JSON: RU/EN ответы о хлоропластах, корректный
  единственный source, `done_reason=stop`, пустой thinking. Cold RU request около 11.7 s,
  warm EN около 1.1 s на этой машине, около 38 generated tokens/s.

После реализации отдельно проверена цепочка production adapters:
`PypdfDocumentParser -> HuggingFaceTokenizer -> OllamaEmbeddingProvider -> OllamaAnswerGenerator`
с cached публичным Qwen tokenizer и реальным localhost daemon. RU/EN запросы дали 768
finite coordinates с norm 1.0, relevant cosine 0.6449/0.6654 и корректные ответы о
хлоропластах с одной проверенной citation. Использовались только синтетические учебные
примеры; резюме с личными данными не индексировалось.

После добавления few-shot примеров в rewrite prompt реальный Qwen сохранил тему RU/EN
follow-up о gradient descent: запрос «Покажи числовой пример из конспекта.» преобразован
в «Какой числовой пример градиентного спуска приведён в конспекте?». EN запрос также
включил gradient descent; явный новый вопрос `What is probability?` остался вопросом о
probability без добавления старой темы. Это проверка трёх конкретных случаев, а не
оценка качества на полном benchmark.

Также проверена цепочка rewrite -> embed -> retrieve -> generate на синтетическом
`examples/materials/gradient-descent-ru.md`. Для указанного RU follow-up rewritten query
содержала gradient descent, scores двух chunks составили 0.5487 и 0.4805, оба превысили
неизменённый порог 0.35. Grounded answer вернул `w_next = 4 - 0.1 * 8 = 3.2`,
`sufficient_context=true` и одну валидную citation. Число 3.2 проверено assertion.

Unit regression suite:

```sh
.venv/bin/pytest -q tests/unit/test_file_storage.py tests/unit/test_parser.py \
  tests/unit/test_tokenizer.py tests/unit/test_embeddings.py tests/unit/test_generation.py
```

Проверяются atomic generated storage paths, traversal/symlink escape, size caps,
UTF-8/BOM и PDF page mapping, scan/invalid input refusal, Unicode/tail/overlap windows,
formatted budgets, retry boundaries, dimension/NaN/zero rejection, no-truncation,
HF device resolution, bounded history/context и citation validation.

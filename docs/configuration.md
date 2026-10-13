# Конфигурация

Источник defaults и проверок: [Settings](../src/study_rag/config.py). Таблица сверена непосредственно с Pydantic fields 13 октября 2026 года; обязательные значения не берутся из частной .env

## Загрузка настроек

Поля имеют prefix STUDY_RAG_, читаются из окружения и .env в текущем рабочем каталоге. Окружение процесса приоритетнее .env; запуск из корня важен для относительных путей. Неизвестные .env поля игнорируются, опечатка не обязательно вызовет ошибку

После изменения настроек перезапустите backend. .env.example содержит распространённые параметры, таблица ниже - все Settings. Секреты не коммитятся, HF/JWT токены не выводятся для диагностики

## Все параметры

Колонка «Ограничение» содержит численные Pydantic bounds; enum и дополнительные проверки описаны отдельно. Секунды, байты и символы не взаимозаменяемы

| Переменная | Default | Ограничение | Назначение |
| --- | --- | --- | --- |
| `STUDY_RAG_DATABASE_URL` | `обязательно` | - | URL PostgreSQL с драйвером postgresql+asyncpg |
| `STUDY_RAG_JWT_SECRET` | `обязательно` | - | Минимум 32 UTF-8 байта; пустой пример не позволяет startup |
| `STUDY_RAG_JWT_ISSUER` | `study-rag` | - | JWT iss |
| `STUDY_RAG_JWT_AUDIENCE` | `study-rag-api` | - | JWT aud |
| `STUDY_RAG_JWT_EXPIRY_SECONDS` | `3600` | >= 60, <= 86400 | Срок access token, секунд; refresh отсутствует |
| `STUDY_RAG_UPLOAD_DIRECTORY` | `.data/uploads` | - | Оригиналы под сгенерированными ключами |
| `STUDY_RAG_MAXIMUM_UPLOAD_BYTES` | `104857600` | >= 1024 | Серверный предел файла; UI независимо ограничен 100 MiB |
| `STUDY_RAG_MAXIMUM_PDF_PAGES` | `5000` | >= 1 | Все страницы PDF, включая пустые |
| `STUDY_RAG_MAXIMUM_EXTRACTED_CHARACTERS` | `20000000` | >= 1 | Суммарный извлечённый текст документа |
| `STUDY_RAG_MAXIMUM_DOCUMENT_CHUNKS` | `100000` | >= 1 | Максимум chunks одного документа |
| `STUDY_RAG_QDRANT_URL` | `не задано` | - | Не задавать для embedded; URL для Qdrant server |
| `STUDY_RAG_QDRANT_DIRECTORY` | `.data/qdrant` | - | Локальный индекс, используется в embedded mode |
| `STUDY_RAG_MODEL_CACHE_DIRECTORY` | `.data/models` | - | HF tokenizer и optional HF model cache |
| `STUDY_RAG_EMBEDDING_BACKEND` | `ollama` | - | ollama или huggingface |
| `STUDY_RAG_EMBEDDING_MODEL` | `google/embeddinggemma-300m` | - | HF embedding model, только HF backend |
| `STUDY_RAG_EMBEDDING_DEVICE` | `auto` | - | auto/cpu/mps/cuda, только HF embeddings |
| `STUDY_RAG_TOKENIZER_MODEL` | `Qwen/Qwen3.5-4B` | - | Tokenizer генерации; для Ollama также chunk tokenizer |
| `STUDY_RAG_CHUNK_BYTE_LIMIT` | `1536` | >= 64, <= 1536 | UTF-8 bytes content в Ollama profile; HF этот предел не применяет |
| `STUDY_RAG_OLLAMA_EMBEDDING_MODEL` | `embeddinggemma:300m` | - | Точный тег embedding-модели Ollama |
| `STUDY_RAG_OLLAMA_URL` | `http://127.0.0.1:11434` | - | Адрес Ollama для adapters |
| `STUDY_RAG_GENERATION_MODEL` | `qwen3.5:4b` | - | Точный тег модели ответов/планирования/review |
| `STUDY_RAG_CHUNK_TOKEN_LIMIT` | `512` | >= 16, <= 1536 | Content tokens по tokenizer выбранного embedding backend |
| `STUDY_RAG_CHUNK_OVERLAP_TOKENS` | `64` | >= 0 | Перекрытие окон, меньше chunk_token_limit |
| `STUDY_RAG_RETRIEVAL_LIMIT` | `6` | >= 1, <= 24 | Контекст focused; comparison до 2x, расширение до 3x; максимум 48 |
| `STUDY_RAG_RETRIEVAL_CANDIDATES` | `24` | >= 1, <= 100 | Векторные кандидаты одного поискового запроса |
| `STUDY_RAG_OVERVIEW_CHUNK_LIMIT` | `40` | >= 8, <= 48 | Выборка для обзора, дополнительно ограничена временем |
| `STUDY_RAG_SCAN_CHUNK_LIMIT` | `20000` | >= 256, <= 100000 | Максимум прочитанных SQL chunks проекта при одном scan |
| `STUDY_RAG_MAXIMUM_SEARCH_PASSES` | `3` | >= 1, <= 4 | Максимум поисковых формулировок vector branch |
| `STUDY_RAG_MINIMUM_SIMILARITY` | `0.35` | >= -1.0, <= 1.0 | Порог cosine; lexical scan его не использует |
| `STUDY_RAG_WORKER_POLL_SECONDS` | `1.0` | > 0.0 | Интервал ingestion worker; не меняет answer worker/GUI |
| `STUDY_RAG_DOCUMENT_LEASE_SECONDS` | `120` | >= 10 | Lease индексации и очистки |
| `STUDY_RAG_HEARTBEAT_SECONDS` | `30` | >= 1 | Heartbeat документов; не turn heartbeat |
| `STUDY_RAG_TURN_LEASE_SECONDS` | `300` | >= 30 | Lease активного ответа, без heartbeat |
| `STUDY_RAG_GENERATION_TIMEOUT_SECONDS` | `120.0` | > 0.0 | Bootstrap ограничивает generation operation 120 с, embedding attempt 40 с |
| `STUDY_RAG_ANSWER_TIMEOUT_SECONDS` | `240.0` | > 0.0 | Бюджет обработки после выхода из очереди |
| `STUDY_RAG_GENERATION_CONTEXT_TOKENS` | `16384` | >= 2048 | num_ctx Qwen; не embedding context |
| `STUDY_RAG_GENERATION_OUTPUT_TOKENS` | `2048` | >= 16 | num_predict первой попытки; length повтор до удвоенного бюджета |
| `STUDY_RAG_HISTORY_MESSAGE_LIMIT` | `8` | >= 0, <= 40 | Последние completed сообщения, user и assistant вместе |

## Совместные ограничения

- DATABASE_URL начинается с postgresql+asyncpg://
- JWT_SECRET содержит минимум 32 UTF-8 байта
- CHUNK_OVERLAP_TOKENS < CHUNK_TOKEN_LIMIT
- HEARTBEAT_SECONDS < DOCUMENT_LEASE_SECONDS
- RETRIEVAL_LIMIT <= RETRIEVAL_CANDIDATES
- ANSWER_TIMEOUT_SECONDS + 10 < TURN_LEASE_SECONDS
- GENERATION_TIMEOUT_SECONDS <= ANSWER_TIMEOUT_SECONDS
- GENERATION_OUTPUT_TOKENS + 512 < GENERATION_CONTEXT_TOKENS

Нарушение прерывает создание Settings, а не исправляется автоматически. Для embedded Qdrant переменную URL нужно убрать, а не задавать пустую строку

## Что фиксировано в реализации

| Значение | Применение |
| --- | --- |
| Вектор 768 | Settings.vector_dimensions; env-настройки нет |
| Embedding formatted input 1900 bytes / 2048 tokens | Ollama adapter/tokenizer, truncate=false |
| PDF decompressed stream 20 млн bytes | Parser, на одну страницу |
| Multipart overhead 65536 bytes | HTTP body limit сверх файла |
| Embedding batch 16 | IngestionWorker |
| Answer worker polling 0.5 с | AnswerQueue |
| GUI answer polling 1 с | После предыдущего запроса |
| GUI document polling 2.5 с | Пока есть queued/indexing/deleting |
| Generator history 2048 tokens | Дополнительно к HISTORY_MESSAGE_LIMIT |
| Prompt reserve 512 tokens | Резерв шаблона |
| Grounded / general JSON answer | 16000 / 4000 символов |
| Sources максимум 48 | Generator schema и search caps |
| Evidence batch 4 / semantic review 12 | Обзор и проверка смысла |
| Каталог максимум 160 пунктов | AdaptiveSearch |
| Excerpt максимум 1000 символов | ConversationService |
| Вопрос максимум 4000 символов | REST/application/UI |
| GUI файл максимум 100 MiB | Независимо от Settings |

Model HTTP transport делает максимум три попытки. Length повтор отдельно регенерирует полный JSON один раз, до min(2 * output budget, остаток контекста); общий timeout turn остаётся в силе

## Изменение профиля

Смена embedding backend/model/tokenizer/chunk settings меняет embedding_profile; вручную его не задают. Материалы надо загрузить заново под новым profile

Generation model, output/context limits, similarity и ширина поиска в profile не входят. Их изменение не требует новых embeddings. Изменение parser также не меняет profile: ready документы нужно явно rebuild

[Модели](local-models.md), [поиск](adaptive-search.md), [PDF](pdf-sources.md), [очередь](answer-queue.md)

## Frontend и внешние переменные

VITE_API_BASE_URL задаётся при Vite build, не через Settings. Default пуст: тот же origin. Разрешён только same-origin path без схемы, //, query, fragment и backslash. Path prefix ApiClient сам не настраивает reverse proxy или Vite base для assets

HF_HUB_OFFLINE=1 разрешает только скачанные HF-артефакты, TRANSFORMERS_OFFLINE=1 относится к Transformers. Offline flags не заменяют Ollama daemon. HF_TOKEN / hf auth используются библиотеками HF

TEST_DATABASE_URL используется только integration fixtures, не приложением. Подробности: [CI](ci.md)

Реализация: [bootstrap](../src/study_rag/bootstrap.py), [transport](../src/study_rag/adapters/model_support.py), [frontend API](../frontend/src/api.ts)

# Архитектура Mini-RAG, первая версия

Статус: первичная архитектура перед написанием реализации, 4 октября 2026 года.
Основание: [requirements.md](requirements.md); точные интерфейсы: [contracts.md](contracts.md).
Проект реализуется в этом репозитории. Стефания, её файлы, навыки и базы знаний не используются.

Разрешённый следующий этап GUI и официальной Pydantic AI AG-UI интеграции
описан в [frontend-architecture.md](frontend-architecture.md). Ниже сохранена
архитектура исходного API MVP; GUI расширяет его существующие сценарии.

## Целевой результат

Пользователь регистрируется, загружает собственные TXT/Markdown/PDF с текстовым слоем,
наблюдает состояние индексации, создаёт чат и получает короткие ответы на русском или
английском с проверяемыми ссылками на фрагменты собственных документов. История сохраняется
между перезапусками. Уточнение в текущем чате использует предыдущие сообщения для понимания
вопроса; фактическим источником ответа остаются найденные материалы.

API и Swagger `/docs` — интерфейс MVP. OCR, фотографии, рукописные записи, web UI,
администрирование, refresh tokens, Redis, Celery и OpenRouter в MVP не входят.

## Стек и локальный запуск

- Python 3.12, uv, FastAPI, Uvicorn, Pydantic, pydantic-settings.
- PostgreSQL, SQLAlchemy 2 async ORM, asyncpg, Alembic. PostgreSQL — источник истины;
  SQLite не заменяет его в подтверждении готовности проекта.
- Qdrant: локальное дисковое хранилище qdrant-client для запуска без Docker; тот же
  адаптер поддерживает URL настоящего Qdrant server. Один процесс приложения при local mode.
- Hugging Face tokenizers для публичного tokenizer `Qwen/Qwen3.5-4B`; локальный Ollama
  `embeddinggemma:300m` для запуска без gated HF весов. Полноценный opt-in backend
  Sentence Transformers с `google/embeddinggemma-300m` и её собственным HF tokenizer.
  Нормализованные 768-мерные векторы; profiles backend/tokenizer не смешиваются.
- Генерация: локальный Ollama, модель `qwen3.5:4b`, исходная модель `Qwen/Qwen3.5-4B`.
  httpx AsyncClient, нестриминговый `/api/chat`, `think: false`; только итоговое содержание.
- Argon2 через pwdlib, JWT через PyJWT; секрет JWT задаётся окружением, минимум 32 байта.
- Ruff, строгий mypy, pytest/pytest-asyncio; правила собственного кода из извлечённого архива.

Основная машина: Apple M3 Pro, 36 GB общей памяти. При выбранном HF backend embeddings float32,
MPS при поддержке, CPU как явно выбранный fallback устройства. Генерация на Metal через
Ollama. Никакой fp16 для EmbeddingGemma. Проверен работающий daemon, загрузки моделей
ещё выполняются; архитектура не обещает конкретное время ответа или качество.

Для воспроизводимости добавить Docker Compose с PostgreSQL и Qdrant, но Ollama запускается
на хосте. Native local mode использует установленный PostgreSQL или отдельно поднятый сервер;
Native установка PostgreSQL 17 может держать cluster в `.local/` проекта без автозапуска
глобального сервиса. Отсутствие Docker — не причина менять стек.
Сервис слушает `127.0.0.1`, один Uvicorn worker. Перед стартом выполняется `alembic upgrade head`.
Lifespan не создаёт таблицы и не запускает миграции автоматически.

## Границы компонентов

```text
HTTP API → authentication dependency → application scenarios → typed domain ports
                                                    ├─ PostgreSQL repositories
                                                    ├─ filesystem storage / PDF parser
                                                    ├─ HF tokenizer / embeddings
                                                    ├─ Qdrant vector search
                                                    └─ Ollama answer generator
Lifespan → durable ingestion worker → те же typed ports
```

HTTP-обработчики валидируют DTO, получают identity и вызывают сценарий. SQL, промпты,
управление lease и разбор PDF не попадают в routes. Небольшие классы сценариев ведут
читаемые этапы; зависимости передаются явно. Protocol существует на внешней границе,
а не для каждого helper. Внутренние DTO — dataclasses с `kw_only`, `slots`, `frozen`;
коллекции в них — tuple. Pydantic используется на HTTP и ответах внешних сервисов.

## Структура проекта и владение файлами

| Область | Файлы | Ответственный в swarm |
| --- | --- | --- |
| Контракты и сборка | `src/study_rag/domain/{entities,ports,errors}.py`, `config.py`, `bootstrap.py`, `main.py`, `pyproject.toml`, CI, README, `.env.example`, Compose | root / foundation |
| Сценарии и API | `application/{authentication,documents,ingestion,conversations}.py`, `api/{schemas,dependencies,routes}.py`, unit/API tests | application agent |
| Хранение и безопасность | `adapters/{database,repositories,security,vector_store}.py`, `migrations/`, PostgreSQL/Qdrant integration tests | persistence agent |
| Модели и чтение | `adapters/{file_storage,document_parser,tokenizer,embeddings,generation}.py`, parser/model boundary tests | models agent |

Каталоги могут иметь `__init__.py`; публичные импорты не выполняют I/O. Не создавать
`BaseService`, универсальный pipeline engine или большой `services.py`.

Порядок swarm: архитектор фиксирует эти два документа → root материализует shared DTO/ports
и tooling → три независимых исполнителя работают только в своих областях → root собирает
composition root и запускает проверки → независимое ревью tenant isolation, состояний,
инференса и правил кода → исправления. Изменение общего контракта сначала согласуется
между агентами и отражается в документации; параллельные правки одного файла исключаются.

## Авторизация и изоляция пользователей

`TenantIdentity.user_identifier` — единственный tenant ID. Клиент не передаёт владельца
в upload/search/message DTO. Он определяется из проверенного JWT и существующей учётной записи.
Email нормализуется, уникален. Пароли 12–128 символов; хранятся только Argon2 hashes.
JWT содержит `sub`, `exp`, `iat`, `iss`, `aud`; разрешён только заданный HS256. TTL 60 минут.
Регистрация и login возвращают токен; Swagger использует HTTP Bearer security scheme.

Все repository операции с пользовательскими объектами требуют tenant ID. Запрос чужого
документа, фрагмента, чата или сообщения возвращает тот же 404, что отсутствующий объект.
Qdrant query/delete всегда содержит tenant filter. Qdrant недоступен пользователю напрямую.
Совпадение UUID ресурса или знание URL не даёт доступ. Оригинальное имя файла не участвует
в построении дискового пути. Логи не содержат пароли, JWT и полный текст документов.

## Данные PostgreSQL

| Таблица | Основные поля и ограничения |
| --- | --- |
| `user_accounts` | UUID PK, normalized email UNIQUE, password hash, created timestamp |
| `documents` | UUID PK, tenant FK, filename/media type, relative storage key, SHA-256/byte count, state, embedding profile, page/chunk counts, lease UUID/deadline, next attempt timestamp, attempts, error code/message, timestamps |
| `document_chunks` | UUID PK, tenant FK, document FK, ordinal, text, token count, page start/end; UNIQUE(document, ordinal) |
| `chat_sessions` | UUID PK, tenant FK, title, created/updated timestamps |
| `chat_turns` | UUID PK, tenant/chat FK, client request UUID, pending/completed/failed, lease UUID/deadline, error; UNIQUE(chat, client request UUID) |
| `chat_messages` | UUID PK, tenant/chat/turn FK, user/assistant role, text, citation snapshots JSONB, created timestamp; UNIQUE(turn, role) |

Composite ownership constraints либо проверки в той же SQL-транзакции предотвращают
связывание chunk/message/turn с объектом другого tenant. UTC timestamps timezone-aware.
Индексы: tenant+created time, documents state+lease deadline, chunks tenant+document,
messages chat+created time. Оригинальные файлы находятся в `.data/uploads/{tenant}/{document}/`.
Qdrant содержит производный индекс, а тексты фрагментов читаются из PostgreSQL.

Deduplication: одинаковое SHA-256 в рамках одного tenant и embedding profile среди не удалённых документов
возвращает существующий документ. Повтор upload не запускает новую индексацию. Уникальный
частичный индекс `(tenant_identifier, content_digest, embedding_profile) WHERE state_name <> 'deleted'`
защищает от гонки; временный файл проигравшей загрузки удаляется. Между tenant dedup нет.
Повторная загрузка того же файла после смены profile создаёт новый документ с новым UUID.

## Надёжная загрузка и индексация

Upload: проверить расширение и лимит → сохранить файл атомарным rename → зарегистрировать
`queued` в PostgreSQL → вернуть 202. Если DB не сохранила документ, удаляется созданный файл.
Предварительное чтение ограничено 100 MiB; лимит контролируется во время чтения, а не
только по `Content-Length`. Потерянный после аварии файл можно удалить обслуживанием;
не существует ответа success без сохранённой строки документа.

Состояния: `queued → indexing → ready`; ошибка `indexing → failed`; ручной retry
`failed → queued`; deletion из любого живого состояния `→ deleting → deleted`.
Удалённый объект не попадает в обычные списки и возвращает 404. `deleting` сразу
исключается из поиска; файл и vectors удаляются отложенно, статус доступен до окончания.

Очередь — таблица documents, не FastAPI BackgroundTasks и не in-memory queue.
Один worker в lifespan раз в секунду забирает `queued`, `deleting` или job с истёкшим
lease. Claim — короткая транзакция с `FOR UPDATE SKIP LOCKED`, новый lease UUID,
deadline 120 секунд. Heartbeat каждые 30 секунд, compare-and-set по UUID. Утрата lease
прекращает работу. Lease не удерживает SQL-транзакцию на протяжении инференса.

Индексация: прочитать файл → извлечь sections → разбить tokenizer-ом → заменить SQL chunks
при действующем lease → удалить старые points документа → batches по 16 вычислить embedding
и upsert vectors → отметить `ready` только после успешного последнего upsert. Chunk UUID
детерминирован из document UUID, embedding profile и ordinal; повтор не создаёт duplicates.
Число chunks сохраняется в PostgreSQL, и final transition проверяет lease/state. Любое
исключение на внешней worker границе сохраняет безопасный код ошибки и traceback в лог.

После аварии устаревшая indexing job повторяется с начала. Частичные points и chunks
никогда не видны при поиске. Транзакции PostgreSQL и Qdrant не объявляются атомарными.
Idempotent upsert/delete и SQL visibility gate обеспечивают восстановление без 2PC.
Временные timeout/HTTP 5xx операции допускают максимум три попытки с backoff и jitter;
ошибки формата и недоступная модель становятся `failed` без бесконечного retry.

Delete endpoint атомарно ставит tombstone `deleting` и отзывает indexing lease. Worker
последовательно удаляет points по tenant+document, файл, chunks и отмечает `deleted`.
SQL hydration защищает даже от point, оставшегося после отменённого сетевого запроса.
Очистка повторяется безопасно; failure cleanup остаётся `deleting` с ошибкой/backoff.
`next_attempt_at` предотвращает немедленный повтор каждую секунду при недоступной зависимости.

## Извлечение, tokenizer и embeddings

TXT и Markdown: UTF-8/UTF-8 BOM, Markdown хранится как текст. Ошибка кодировки не
маскируется произвольным decode. PDF: pypdf, по страницам; page numbers 1-based.
Пустой/сканированный PDF без текста получает `failed: no_extractable_text`.
Ограничения: до 5000 страниц, 20 миллионов извлечённых символов, 100 тысяч chunks на документ.

Chunking выполняется на Hugging Face tokenizer. Для доступного HF EmbeddingGemma backend
это собственный tokenizer модели, для Ollama default — публичный Qwen tokenizer. Различие
явно входит в profile и документацию: `token_count` относится к tokenizer profile и не
выдаётся за измерение Gemma tokens при использовании Qwen. Начальные настройки: 512
content tokens, overlap 64; chunk не смешивает PDF страницы. Для Ollama дополнительно
ограничить content 1536 UTF-8 bytes и полный formatted input 1900 bytes; это консервативный
бюджет, окончательная защита от переполнения — `truncate: false` с явной ошибкой.
Для HF считать полный prefix и специальные tokens собственным tokenizer и проверять 2048.
Silent truncation запрещён. Не терять последний фрагмент и не зацикливаться на overlap;
передаваемый text соответствует сохранённому chunk.

Retrieval prefixes: query `task: search result | query: `, document
`title: {title-or-none} | text: `. Имя файла используется как ограниченный title;
вызов Sentence Transformers не должен добавлять этот prefix второй раз. Вектор проверяется
на размерность, finite values и ненулевую норму, затем нормализуется.
Это следует [официальной карточке EmbeddingGemma](https://huggingface.co/google/embeddinggemma-300m).

HF модель требует принятой лицензии и доступа к gated files; unauthenticated запрос
tokenizer.json вернул 401. Этот tokenizer не является безусловным startup gate. Opt-in HF
backend сообщает причину отсутствия доступа. Default backend Ollama использует оригинальную
EmbeddingGemma в локальном артефакте `embeddinggemma:300m`, Qwen HF tokenizer применяется
к чанкам и budget генерации. `/api/embed` не применяет Modelfile template: adapter добавляет
query/document prefix вручную. `/api/embed`
вызывается с `truncate: false`, согласно [Ollama API](https://docs.ollama.com/api/embed).
Никакого автоматического переключения на fake/другую модель. HF и Ollama/quantized profiles
имеют разные коллекции/профили; их векторы не смешиваются. Tokenizer id и byte budget входят
в profile. Смена backend/profile требует
переиндексации. HF adapter остаётся полноценной поддержанной реализацией.

## Qdrant и поиск

Одна коллекция на embedding profile, distance COSINE, 768 dimensions. Payload:
`tenant_identifier`, `document_identifier`, `chunk_identifier`, `embedding_profile`.
Нужны keyword payload indexes при server mode. Collection config проверяется при старте;
несовместимая коллекция приводит к явной ошибке, а не скрытому recreate с потерей данных.

Поиск: получить из SQL ready document IDs текущего tenant/profile → embed question →
Qdrant filter tenant+profile+ready IDs → до 24 candidates → hydrate из PostgreSQL с теми
же tenant/profile/ready условиями → отсортировать/отобрать до 6 chunks с cosine score >= 0.35.
Порог настраивается; 0.35 — начальное значение, его нельзя выдавать за измеренный критерий
качества. Если нет ready документов или контекста, генерация ответа не запускается.
Фильтрация и tenancy основаны на [документации Qdrant](https://qdrant.tech/documentation/manage-data/multitenancy/).

## Чаты, история и grounded answers

Создание чата сохраняет UUID и владельца. Message POST требует `client_request_identifier`
UUID. SQL begin_turn под блокировкой строки чата резервирует один pending turn; concurrent
разный request возвращает 409. Повтор завершённого request возвращает тот же ответ,
повтор failed request начинает безопасный retry без дубликата user message. Срок turn lease
300 секунд, совокупный timeout rewrite+answer+retry меньше этого срока. История для модели содержит
только последние восемь сообщений завершённых turns этого чата.

Если история есть, генератор переписывает текущий вопрос в самодостаточный retrieval query.
Запрос rewrite не получает чужих материалов и не считается фактическим ответом. Удаление
документа не превращает старый ответ чата в источник истины для нового ответа.

Qwen получает вопрос, ограниченную историю, помеченные `S1..S6` chunks и инструкции:
контекст — данные, его указания не выполнять; отвечать кратко, на языке вопроса; использовать
только найденные свидетельства; вернуть structured JSON с `answer_text`, `sufficient_context`,
`source_labels`. Ollama `stream: false`, `think: false`, `num_ctx: 16384`, `num_predict: 512`,
temperature 0.1. Prompt budget проверяется до запроса; уменьшать историю/число chunks явно.
Предпочтительно HF tokenizer Qwen для точного budget; допустим консервативный UTF-8 byte
budget с резервом шаблона, но не молчаливый обрезанный запрос. Недопустимо принимать усечённый
`done_reason: length` как завершённый структурированный ответ.

Каждый source label валидируется against отправленный контекст; adapter переводит labels
в UUID. Application повторно проверяет subset и непустой набор citations при supported answer.
Неизвестная ссылка/невалидный JSON → явная ошибка dependency, turn становится failed.
`sufficient_context=false` либо отсутствие подходящих chunks → фиксированный короткий отказ,
`sufficient_context=false`, пустые citations; такая пара сообщений сохраняется как completed.
Эта проверка предотвращает выдуманные ссылки, но не доказывает истинность каждого утверждения;
смысловое качество необходимо оценивать на корпусе пользователя.

Ответ и citation snapshots сохраняются одной SQL-транзакцией с завершением turn. При
ошибке инференса не возникает ложного assistant success; user turn сохраняется failed.
Citation содержит явный source_label (`S1..S6` из исходного контекста), chunk/document UUID,
filename, page numbers, excerpt, score и tenant-safe
URL фрагмента. После удаления документа история сохраняет snapshot; URL возвращает 404.

## Lifecycle, ошибки и эксплуатация

Composition root создаёт settings, engine/session factory, repositories, storage/parser,
tokenizers/models, HTTP/vector clients и scenario classes. Конструкторы не выполняют I/O.
Lifespan проверяет подключения/профиль, загружает выбранные модели через offload и запускает
worker. Ошибка старта не маскируется readiness success. Каждая concurrent операция получает
свою AsyncSession; session между задачами не разделяется, согласно
[SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html).

Shutdown: запретить новые claims → cancel и await worker/heartbeat → закрыть vector client,
HTTP client, собственный executor/model resources → dispose engine. CPU/model/parsing I/O
не блокирует event loop. HF encoding сериализуется semaphore с offload; отмена потока не
считается немедленным прекращением вычисления, поэтому после неё side effects не продолжаются.

HTTP ошибки: 401 credentials/token; 404 missing/foreign; 409 duplicate email/chat busy/
invalid state; 413 upload too large; 415 format; 422 validation; 503 unavailable dependency.
Ошибки имеют стабильный `error_code` и безопасный `message_text`. Worker errors хранятся
для документа; не включать секреты/полные внешние ответы. Health endpoint проверяет readiness
PostgreSQL, vector store и выбранных model adapters, не запускает полноценную генерацию.

## Приёмка и проверки

1. API register/login/upload/status/chat/message/history/chunk/delete/retry и `/docs` работают.
2. После перезапуска сохранены пользователи, документы, история; queued/expired jobs возобновляются.
3. Два tenant с одинаковым файлом не видят чужие объекты, query results и citations.
4. TXT/MD/PDF text layer превращаются в корректные page-aware chunks без потери хвоста;
   scan/invalid encoding/oversized inputs получают объяснимую ошибку.
5. Частичный upsert/exception/retry не даёт searchable partial documents или duplicates;
   delete немедленно исключает документ из ответа.
6. Без контекста сервис отказывается; fake model boundary проверяет citations/history/idempotency,
   но не выдаётся за проверку настоящих моделей.

Meaningful unit tests: chunk boundaries/overlap/full-token limit, parsing страниц, prefix
единожды, vector validation, citations subset и no-context early return. Scenario/API tests:
auth, two-tenant isolation, history follow-up, request deduplication, failed dependencies,
restart/delete/retry. Реальные PostgreSQL+Qdrant integration tests отдельно проверяют constraints,
claim concurrency/CAS и durable state; запуск последовательный, пока нет test resource isolation.
Отдельный opt-in local smoke выполняет реальный EmbeddingGemma и Qwen3.5-4B, upload small
RU/EN fixtures, retrieval+citations+history и refusal. В README записываются реальные команды
и результаты; skipped integration/model tests обозначаются явно.

Пользовательского корпуса нет. Добавить маленькие собственные RU/EN fixtures для проверки
поведения и шаблон evaluation JSONL: question, expected document/page, required facts,
unanswerable. Не заявлять recall/accuracy до получения корпуса и измерения.

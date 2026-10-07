# Контракты Mini-RAG для параллельной реализации

Спецификация интерфейсов, не код реализации. Определения находятся в
`src/study_rag/domain/entities.py`, Protocol — `ports.py`, ошибки — `errors.py`.
Агенты импортируют эти определения; не создают альтернативные DTO с похожими полями.
UUID означает `uuid.UUID`, Timestamp — aware `datetime.datetime`, Vector — `tuple[float, ...]`.
Все ниже приведённые параметры именованные (`*`), если явно не указан внешний контракт.

## Общие DTO

| Тип | Поля |
| --- | --- |
| `TenantIdentity` | `user_identifier: UUID`, `email_address: str` |
| `UserAccount` | `user_identifier: UUID`, `email_address: str`, `password_hash: str`, `created_at: Timestamp` |
| `AuthToken` | `access_token: str`, `token_type: str = "bearer"`, `expires_in: int` |
| `PageResult[T]` | `items: tuple[T, ...]`, `total_count: int`, `offset_value: int`, `limit_count: int` |
| `DocumentState` | string Enum: `queued`, `indexing`, `ready`, `failed`, `deleting`, `deleted` |
| `StoredUpload` | `storage_key: str`, `content_digest: str`, `byte_count: int` |
| `DocumentRegistration` | `document_identifier: UUID`, `filename_text: str`, `media_type: str`, `storage_key: str`, `content_digest: str`, `byte_count: int`, `embedding_profile: str` |
| `DocumentRecord` | registration fields + `tenant_identifier: UUID`, `state_name: DocumentState`, `chunk_count: int`, `page_count: int | None`, `error_code: str | None`, `error_message: str | None`, `created_at: Timestamp`, `updated_at: Timestamp` |
| `DocumentLease` | `document_record: DocumentRecord`, `lease_identifier: UUID`, `action_name: Literal["index", "delete"]` |
| `TextSection` | `content_text: str`, `page_number: int | None` |
| `ExtractedDocument` | `text_sections: tuple[TextSection, ...]`, `page_count: int | None` |
| `ChunkRecord` | `chunk_identifier: UUID`, `document_identifier: UUID`, `tenant_identifier: UUID`, `ordinal_value: int`, `content_text: str`, `token_count: int`, `page_start: int | None`, `page_end: int | None` |
| `EmbeddedChunk` | `chunk_record: ChunkRecord`, `embedding_vector: Vector` |
| `VectorMatch` | `chunk_identifier: UUID`, `similarity_score: float` |
| `RetrievedChunk` | `chunk_record: ChunkRecord`, `filename_text: str`, `similarity_score: float` |
| `CitationRecord` | `source_label: str` (`S1..S6` position in supplied context), `chunk_identifier: UUID`, `document_identifier: UUID`, `filename_text: str`, `page_start: int | None`, `page_end: int | None`, `excerpt_text: str`, `similarity_score: float`, `source_url: str` |
| `ChatRecord` | `chat_identifier: UUID`, `tenant_identifier: UUID`, `title_text: str`, `created_at: Timestamp`, `updated_at: Timestamp` |
| `MessageRecord` | `message_identifier: UUID`, `chat_identifier: UUID`, `turn_identifier: UUID`, `role_name: Literal["user", "assistant"]`, `content_text: str`, `citation_records: tuple[CitationRecord, ...]`, `sufficient_context: bool | None`, `turn_status: Literal["pending", "completed", "failed"]`, `created_at: Timestamp` |
| `TurnRecord` | `turn_identifier: UUID`, `chat_identifier: UUID`, `client_request_identifier: UUID`, `status_name: Literal["pending", "completed", "failed"]`, `lease_identifier: UUID | None`, `user_message: MessageRecord`, `assistant_message: MessageRecord | None` |
| `GeneratedAnswer` | `answer_text: str`, `sufficient_context: bool`, `citation_identifiers: tuple[UUID, ...]` |
| `AnswerRecord` | `turn_identifier: UUID`, `chat_identifier: UUID`, `user_message: MessageRecord`, `assistant_message: MessageRecord` |

`PageResult.items` и `AuthToken.token_type` — утверждённые внешние имена с исключением
минимальной длины. Enum members — внешние значения состояний. `created_at`, `updated_at`,
`page_end` и остальные публичные поля не дополняются бессмысленными символами.
Добавление нового поля требует default, если оно не обязательное по смыслу.

## Domain errors

`DomainError(error_code: str, message_text: str)` имеет конкретные классы:
`InvalidInputError`, `AuthenticationError`, `ResourceNotFoundError`, `ResourceConflictError`,
`DependencyUnavailableError`, `LeaseLostError`. Ошибки формат/size могут быть отдельными
подклассами InvalidInputError либо стабильными кодами для HTTP mapping. Infrastructure
adapter переводит узкие сторонние исключения с `raise ... from ...`; traceback остаётся в лог.

## Репозитории

Каждый метод выполняет собственную короткую транзакцию. Его success означает committed
изменение. Reads возвращают domain DTO, не ORM entities. SQLAlchemy AsyncSession создаётся
на метод/транзакцию через shared session factory, но не хранится между вызовами.

### AuthRepository

- `create_account(*, email_address: str, password_hash: str) -> UserAccount` (async).
- `fetch_by_email(*, email_address: str) -> UserAccount | None` (async).
- `fetch_by_identifier(*, user_identifier: UUID) -> UserAccount | None` (async).

Email UNIQUE collision → ResourceConflictError `email_already_registered`.

### DocumentRepository

- `register_document(*, tenant_identifier: UUID, document_registration: DocumentRegistration) -> DocumentRecord` (async). Idempotent tenant+content digest+embedding profile, может вернуть существующий UUID; changed profile creates new UUID.
- `fetch_document(*, tenant_identifier: UUID, document_identifier: UUID) -> DocumentRecord` (async). Deleted/foreign → 404.
- `list_documents(*, tenant_identifier: UUID, limit_count: int, offset_value: int) -> PageResult[DocumentRecord]` (async). Не включает deleted.
- `fetch_ready_identifiers(*, tenant_identifier: UUID, embedding_profile: str) -> tuple[UUID, ...]` (async).
- `fetch_ready_chunks(*, tenant_identifier: UUID, embedding_profile: str, chunk_identifiers: tuple[UUID, ...]) -> tuple[ChunkRecord, ...]` (async). Only ready matching documents; порядок не обещается.
- `fetch_chunk(*, tenant_identifier: UUID, chunk_identifier: UUID) -> RetrievedChunk` (async). Только ready document; similarity_score=0.0 для direct fetch.
- `request_deletion(*, tenant_identifier: UUID, document_identifier: UUID) -> DocumentRecord` (async). Tombstone и отзыв lease в одной транзакции; повтор deleting idempotent.
- `retry_indexing(*, tenant_identifier: UUID, document_identifier: UUID) -> DocumentRecord` (async). Только failed → queued; queued/indexing повтор возвращает текущее; ready/deleting → 409.
- `claim_document(*, worker_identifier: UUID, lease_seconds: int) -> DocumentLease | None` (async). SKIP LOCKED; queued/expired indexing/deleting и next_attempt_at<=now; state и lease меняются атомарно.
- `renew_lease(*, document_lease: DocumentLease, lease_seconds: int) -> bool` (async). CAS UUID и подходящего state; false означает прекращение работы.
- `replace_chunks(*, document_lease: DocumentLease, chunk_records: tuple[ChunkRecord, ...], page_count: int | None) -> None` (async). CAS; удалить предыдущие chunks и вставить новые одной транзакцией.
- `complete_indexing(*, document_lease: DocumentLease) -> None` (async). CAS indexing → ready; очистить lease/error. Lease lost → LeaseLostError.
- `fail_indexing(*, document_lease: DocumentLease, error_code: str, error_message: str) -> None` (async). Только владелец текущего lease; чужой/stale lease не меняет состояние.
- `complete_deletion(*, document_lease: DocumentLease) -> None` (async). CAS, удалить chunks, state=deleted, очистить lease.
- `fail_deletion(*, document_lease: DocumentLease, error_code: str, error_message: str) -> None` (async). Сохранить error, освободить lease, задать next_attempt_at с bounded backoff; state остаётся deleting.

Worker API не имеет tenant argument, так как работает как внутренняя доверенная задача;
DocumentLease содержит tenant, который обязателен для последующих file/vector операций.

### ConversationRepository

- `create_chat(*, tenant_identifier: UUID, title_text: str) -> ChatRecord` (async).
- `fetch_chat(*, tenant_identifier: UUID, chat_identifier: UUID) -> ChatRecord` (async).
- `list_chats(*, tenant_identifier: UUID, limit_count: int, offset_value: int) -> PageResult[ChatRecord]` (async).
- `list_messages(*, tenant_identifier: UUID, chat_identifier: UUID, limit_count: int, offset_value: int) -> PageResult[MessageRecord]` (async). Chronological, includes failed/pending turn user rows for status.
- `fetch_history(*, tenant_identifier: UUID, chat_identifier: UUID, message_limit: int) -> tuple[MessageRecord, ...]` (async). Последние completed messages, chronological.
- `begin_turn(*, tenant_identifier: UUID, chat_identifier: UUID, client_request_identifier: UUID, question_text: str, lease_seconds: int) -> TurnRecord` (async). Chat row lock; create user+turn or replay completed; pending active same/different request →409; expired/failed same request safe retry. Different question under same request UUID →409.
- `complete_turn(*, tenant_identifier: UUID, turn_record: TurnRecord, generated_answer: GeneratedAnswer, citation_records: tuple[CitationRecord, ...]) -> AnswerRecord` (async). CAS lease; создать assistant, completed, updated chat одной транзакцией.
- `fail_turn(*, tenant_identifier: UUID, turn_record: TurnRecord, error_code: str) -> None` (async). CAS pending → failed, safe repeated/stale handling.

`begin_turn` возвращает pending с lease UUID для нового выполнения, completed с сохранённым
assistant для replay. После expired pending другого request его состояние failed прежде,
чем резервируется следующий turn. User text никогда не перезаписывается другим request.

## Внешние I/O ports

### PasswordSecurity и AccessTokenCodec

- `PasswordSecurity.hash_password(*, password_text: str) -> str` (sync, application offloads CPU).
- `PasswordSecurity.verify_password(*, password_text: str, password_hash: str) -> bool` (sync, offload).
- `AccessTokenCodec.issue_token(*, tenant_identity: TenantIdentity) -> AuthToken` (sync).
- `AccessTokenCodec.decode_token(*, access_token: str) -> UUID` (sync). Проверяет claims/algorithm; bad token → AuthenticationError.

### FileStorage

- `save_upload(*, tenant_identifier: UUID, document_identifier: UUID, content_bytes: bytes, filename_text: str) -> StoredUpload` (async). Atomic generated path, calculate digest.
- `load_document(*, storage_key: str) -> bytes` (async). Проверенный key внутри configured uploads root.
- `delete_document(*, storage_key: str) -> None` (async). Idempotent missing file.

### DocumentParser и TextTokenizer

- `DocumentParser.extract_document(*, content_bytes: bytes, media_type: str) -> ExtractedDocument` (async). Синхронный PDF parser offload внутри adapter.
- `TextTokenizer.count_tokens(*, content_text: str) -> int` (sync). HF tokenizer профиля; политика special tokens фиксирована.
- `TextTokenizer.split_sections(*, tenant_identifier: UUID, document_identifier: UUID, embedding_profile: str, extracted_document: ExtractedDocument, filename_text: str) -> tuple[ChunkRecord, ...]` (async). Deterministic IDs, page-aware 512/64; formatted limit verified; Ollama профиль дополнительно content<=1536 UTF-8 bytes/full input<=1900 bytes.

Tokenizer содержит ограничения/prefix policy в конструкторе; split_sections по смыслу
не делает сохранение. Конкретный класс `HuggingFaceTokenizer` отвечает только за tokenizer
и chunk rule, без обращения к DB/Qdrant.

### EmbeddingProvider

- Readonly properties `embedding_profile: str`, `vector_dimensions: int`.
- `embed_documents(*, chunk_records: tuple[ChunkRecord, ...], filename_text: str) -> tuple[Vector, ...]` (async). Порядок соответствует chunks; prefix единожды.
- `embed_question(*, question_text: str) -> Vector` (async). Query prefix единожды.
- `initialize_provider() -> None` (async), `close_provider() -> None` (async). Explicit lifecycle; constructor no model download.

Concrete `HuggingFaceEmbeddingProvider` и `OllamaEmbeddingProvider` взаимозаменяемы только
в пределах текущего profile; они не обещают равенство vectors между backends.

### VectorStore

- `initialize_store(*, embedding_profile: str, vector_dimensions: int) -> None` (async).
- `upsert_chunks(*, tenant_identifier: UUID, embedding_profile: str, embedded_chunks: tuple[EmbeddedChunk, ...]) -> None` (async). Проверка каждого embedded chunk tenant; point UUID=chunk UUID.
- `delete_document(*, tenant_identifier: UUID, document_identifier: UUID, embedding_profile: str) -> None` (async). Filter оба IDs; idempotent.
- `search_chunks(*, tenant_identifier: UUID, embedding_profile: str, question_vector: Vector, document_identifiers: tuple[UUID, ...], search_limit: int) -> tuple[VectorMatch, ...]` (async). Mandatory ready document match-any filter; empty list → empty result, не unfiltered search.
- `check_readiness() -> bool` (async), `close_store() -> None` (async).

### AnswerGenerator

- `rewrite_question(*, question_text: str, conversation_history: tuple[MessageRecord, ...]) -> str` (async). Bounded self-contained query; no history → application может пропустить.
- `generate_answer(*, question_text: str, conversation_history: tuple[MessageRecord, ...], context_chunks: tuple[RetrievedChunk, ...]) -> GeneratedAnswer` (async). Context label mapping belongs to adapter; typed structured JSON validation.
- `check_readiness() -> bool` (async), `close_provider() -> None` (async).

Конкретный `OllamaAnswerGenerator` получает httpx AsyncClient через constructor. Provider
URL/model — settings, business services не знают Ollama request schema. Future OpenRouter
adapter не добавляется сейчас. Не использовать ответ assistant из history как новый context chunk.

## Сценарии и composition root

Конструкторы используют именованные зависимости, названные как ports в snake_case.
Принятые имена публичных методов:

- `AuthenticationService.register_account(*, email_address: str, password_text: str) -> AuthToken`.
- `AuthenticationService.login_account(*, email_address: str, password_text: str) -> AuthToken`.
- `AuthenticationService.authenticate_token(*, access_token: str) -> TenantIdentity`.
- `DocumentService.upload_document(*, tenant_identity: TenantIdentity, filename_text: str, content_bytes: bytes) -> DocumentRecord`.
- `DocumentService.fetch_document`, `list_documents`, `fetch_chunk`, `delete_document`, `retry_document`: tenant_identity + corresponding IDs/list parameters, delegate domain outputs.
- `ConversationService.create_chat`, `list_chats`, `fetch_chat`, `list_messages`: tenant_identity + corresponding fields.
- `ConversationService.answer_question(*, tenant_identity: TenantIdentity, chat_identifier: UUID, client_request_identifier: UUID, question_text: str) -> AnswerRecord`.
- `IngestionWorker.run_worker() -> None` (async loop), `process_document(*, document_lease: DocumentLease) -> None` (async one job), `stop_worker() -> None` (sync sets stop event).

Все service methods выше async, кроме stop_worker. Worker принадлежит lifespan и не запускает
сам себя в конструкторе. Root создаёт `ApplicationResources` в bootstrap: settings, engine,
clients, authentication_service, document_service, conversation_service, ingestion_worker.
FastAPI app state содержит только этот typed resource bundle; dependencies явно получают
его, без getattr/service locator. Thin `create_application()` composition function создаёт app.

## HTTP contract

Все собственные REST URLs с trailing slash, без цепочек вложенных сущностей.
GUI `/`, собранные файлы `/assets/` и `/favicon.svg`, Swagger `/docs`, OpenAPI `/openapi.json`.
Pagination `limit` 1..100 default 50, `offset` >=0. Response pages:
`{items: [...], total_count: int, offset: int, limit: int}`; `items/limit/offset` external aliases.
UUID JSON strings, timestamp ISO8601 UTC, file content multipart field `file` external name.

| Метод и путь | Вход | Выход |
| --- | --- | --- |
| `POST /rest/study-rag/users/` | JSON `email_address`, `password_text` | 201 AuthToken; 409 duplicate |
| `POST /rest/study-rag/sessions/` | JSON `email_address`, `password_text` | 200 AuthToken; 401 invalid |
| `GET /rest/study-rag/users/current/` | Bearer | 200 TenantIdentity |
| `POST /rest/study-rag/documents/` | Bearer + multipart `file` | 202 DocumentResponse, including dedup existing state |
| `GET /rest/study-rag/documents/` | Bearer + pagination | 200 document page |
| `GET /rest/study-rag/documents/{document_identifier}/` | Bearer | 200 DocumentResponse |
| `DELETE /rest/study-rag/documents/{document_identifier}/` | Bearer | 202 DocumentResponse state deleting |
| `POST /rest/study-rag/indexing-jobs/` | Bearer + JSON `document_identifier` | 202 DocumentResponse |
| `GET /rest/study-rag/chunks/{chunk_identifier}/` | Bearer | 200 ChunkResponse |
| `POST /rest/study-rag/chats/` | Bearer + JSON `title_text` (default `Новый чат`, 1..200 chars) | 201 ChatResponse |
| `GET /rest/study-rag/chats/` | Bearer + pagination | 200 chat page |
| `GET /rest/study-rag/chats/{chat_identifier}/` | Bearer | 200 ChatResponse |
| `GET /rest/study-rag/messages/` | Bearer + `chat_identifier` required + pagination | 200 chronological message page |
| `POST /rest/study-rag/messages/` | Bearer + JSON `chat_identifier`, `client_request_identifier`, `question_text` (1..4000 chars) | 200 AnswerResponse; 409 active turn |
| `GET /rest/study-rag/health/` | public | 200 ready or 503 dependency unready |

HTTP responses не раскрывают storage_key/password_hash/lease/tenant_identifier. DocumentResponse:
`document_identifier`, `filename_text`, `media_type`, `byte_count`, `state_name`, `chunk_count`,
`page_count`, `embedding_profile`, `error_code`, `error_message`, `created_at`, `updated_at`.
ChatResponse — ChatRecord без tenant. ChunkResponse — chunk fields без tenant, плюс filename.
MessageResponse — MessageRecord fields. AnswerResponse — AnswerRecord fields; citations находятся
в assistant_message.citation_records. `source_url` relative REST chunk URL, требует тот же Bearer.
Error JSON `{error_code: str, message_text: str}`. Health JSON `{status_name: "ready"|"unavailable"}`.

Пример supported message content: `Определение ... [S1].`; фактические UUID/URL доступны в
citation_records. Генератор не составляет произвольные URL сам.

## AG-UI contract

`POST /rpc/study-rag/agent/` — защищённый endpoint для официального
`HttpAgent` из `@ag-ui/client==1.0.1`. Запрос использует Bearer JWT,
`Content-Type: application/json` и `Accept: text/event-stream`.
Ответ — SSE, созданный официальным Pydantic AI `AGUIAdapter` из
`pydantic-ai-slim[ag-ui]==2.54.0`, с `Cache-Control: no-cache` и
`X-Accel-Buffering: no`.

Тело валидируется как официальный `RunAgentInput`:

| Поле | Допустимое значение |
| --- | --- |
| `threadId` | UUID существующего чата текущего пользователя |
| `runId` | UUID запроса; соответствует `client_request_identifier` существующего сценария |
| `messages` | 1–100 сообщений; последнее содержит `role: "user"`, непустой `id` и строковый `content` |
| `content` последнего сообщения | После удаления внешних пробелов 1–4000 символов |
| `tools`, `context` | Пустые массивы |
| `state`, `forwardedProps` | Пустые объекты или `null` |
| `resume` | Не используется; непустое значение отклоняется |
| `parentRunId` | Отсутствует или `null` |
| `protocolVersion` | Необязательное единственное extra поле; допускается только строка `"1.0"` |

Любые другие extra поля, непустые tools/context/state/forwardedProps и неподдерживаемая
версия протокола возвращают 422 `invalid_agent_request` до открытия SSE.
Размер всего тела ограничен 64 KiB во время чтения, включая запрос без Content-Length:
превышение — 413 `agent_request_too_large`. Другой Content-Type —
415 `unsupported_agent_content_type`. Некорректные UUID/сообщения — 422.
Токен и принадлежность чата проверяются до SSE; 401 и 404 имеют обычный ErrorResponse.

Предыдущие сообщения клиента не являются авторитетной историей. Сервер передаёт
в adapter только последний проверенный вопрос, а `ConversationService` читает
историю из PostgreSQL. Browser state и thread UUID не определяют tenant.
Pydantic AI `FunctionModel` делегирует один вызов
`ConversationService.answer_question` с серверными tenant, chat, run UUID и вопросом;
дополнительного вызова LLM в транспортной обёртке нет.

Успешный run содержит `RUN_STARTED`, `TEXT_MESSAGE_START`,
`TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END`, `RUN_FINISHED`.
События и кодирование SSE создаёт SDK; число content events не является гарантией
token streaming. Bridge выдаёт только уже сохранённый ответ.
После завершения UI читает `GET /rest/study-rag/messages/` и получает постоянные ID,
`citation_records`, `sufficient_context` и turn statuses. Отдельные custom events
для citations не используются.

Ошибка после начала SSE выражается `RUN_ERROR`: доменная ошибка содержит безопасные
`code` и `message`, неожиданная — `agent_run_failed` без текста исключения.
Клиент перечитывает историю после ошибки/разрыва: закрытие SSE не доказывает откат БД.
Повтор использует тот же `runId` и вопрос. Completed replay и failed retry следуют
существующим правилам `begin_turn`; другой вопрос под тем же UUID и занятый чат дают
конфликт, который после открытия SSE передаётся через `RUN_ERROR`.
Новый вопрос всегда получает новый UUID.

GUI хранит JWT в `sessionStorage`, передаёт его только в Authorization header
и очищает сессию при выходе или 401. Источники открываются authenticated REST fetch
в панели интерфейса; токен не включается в URL. Markdown renderer не исполняет raw HTML.

## Settings contract

`Settings` из pydantic-settings, prefix `STUDY_RAG_`, dotenv `.env` по явному выбору. Поля:
`database_url`, `jwt_secret`, `jwt_issuer="study-rag"`, `jwt_audience="study-rag-api"`,
`jwt_expiry_seconds=3600`, `upload_directory`, `maximum_upload_bytes=104857600`,
`qdrant_url: str|None`, `qdrant_directory`, `embedding_backend: Literal["huggingface","ollama"]="ollama"`,
`embedding_model="google/embeddinggemma-300m"`, `embedding_device="auto"`,
`tokenizer_model="Qwen/Qwen3.5-4B"`, `chunk_byte_limit=1536`,
`ollama_embedding_model="embeddinggemma:300m"`, `ollama_url="http://127.0.0.1:11434"`,
`generation_model="qwen3.5:4b"`, `chunk_token_limit=512`, `chunk_overlap_tokens=64`,
`retrieval_limit=6`, `retrieval_candidates=24`, `minimum_similarity=0.35`,
`worker_poll_seconds=1.0`, `document_lease_seconds=120`, `heartbeat_seconds=30`,
`turn_lease_seconds=300`, `generation_timeout_seconds=120.0`, `generation_context_tokens=16384`,
`generation_output_tokens=512`, `history_message_limit=8`.
HF cache/token задаются стандартными Hugging Face env, секреты никогда не печатаются.
HF backend требует tokenizer модели EmbeddingGemma, независимо от default tokenizer_model.
Profile формируется из backend/model/dimension/prefix/tokenizer/chunk/byte settings; менять
его вручную нельзя. Public Qwen tokenizer читается через huggingface_hub/tokenizers без
загрузки весов Qwen в Python. Production fake fallback запрещён.

## Проверка handoff

Root сначала создаёт entities/ports/errors и передаёт агентах их фактические определения.
Рабочая реализация не обязана создавать файлы заранее пустыми. Обязательны согласованные
imports/signatures, strict type checking и тесты наблюдаемого поведения. Обоснованные
уточнения контракта допустимы, но фиксируются здесь до зависимых правок.

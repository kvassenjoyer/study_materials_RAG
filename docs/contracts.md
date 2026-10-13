# Контракты API и внутренних границ

Сверено 13 октября 2026 года с [routes.py](../src/study_rag/api/routes.py), [schemas.py](../src/study_rag/api/schemas.py), [ag_ui.py](../src/study_rag/api/ag_ui.py). Машиночитаемая схема текущего сервера: `GET /openapi.json`, Swagger: `/docs`

## Общие правила

REST prefix - `/rest/study-rag`, пути с завершающим slash. UUID передаются строками, timestamps - ISO8601. Все пользовательские операции, кроме регистрации/входа, требуют `Authorization: Bearer`. Health публичный

Request DTO запрещают extra поля. Pagination: limit 1-100, default 50; offset >= 0, default 0. PageResponse содержит items, total_count, offset, limit. Active answer jobs возвращаются обычным массивом без pagination

Владелец определяется из JWT и существующего аккаунта, а не из JSON. Чужой/отсутствующий объект даёт 404. Без project_identifier операции выбора проекта используют самый ранний действующий проект; это не запрос по всем проектам

## REST endpoints

В таблице пути относительно `/rest/study-rag`

| Метод | Путь | Вход | Успех |
| --- | --- | --- | --- |
| POST | `/users/` | CredentialsRequest | 201 AuthResponse |
| POST | `/sessions/` | CredentialsRequest | 200 AuthResponse |
| GET | `/users/current/` | Bearer | 200 IdentityResponse |
| POST | `/projects/` | ProjectRequest | 201 ProjectResponse |
| GET | `/projects/` | pagination | 200 PageResponse |
| GET | `/projects/{project_identifier}/` | UUID | 200 ProjectResponse |
| PATCH | `/projects/{project_identifier}/` | ProjectRequest | 200 ProjectResponse |
| DELETE | `/projects/{project_identifier}/` | UUID | 204 |
| POST | `/documents/` | multipart file, query project_identifier optional | 202 DocumentResponse |
| GET | `/documents/` | pagination, query project_identifier optional | 200 PageResponse |
| GET | `/documents/{document_identifier}/` | UUID | 200 DocumentResponse |
| DELETE | `/documents/{document_identifier}/` | UUID | 202 DocumentResponse, deleting |
| GET | `/documents/{document_identifier}/original/` | UUID, только PDF | 200 PDF bytes |
| POST | `/indexing-jobs/` | IndexingRequest | 202 DocumentResponse |
| GET | `/chunks/{chunk_identifier}/` | UUID | 200 ChunkResponse |
| POST | `/chats/` | ChatRequest | 201 ChatResponse |
| GET | `/chats/` | pagination, query project_identifier optional | 200 PageResponse |
| GET | `/chats/{chat_identifier}/` | UUID | 200 ChatResponse |
| PATCH | `/chats/{chat_identifier}/` | ProjectRequest с title_text | 200 ChatResponse |
| DELETE | `/chats/{chat_identifier}/` | UUID | 204 |
| GET | `/messages/` | required query chat_identifier, pagination | 200 PageResponse |
| POST | `/messages/` | QuestionRequest | 200 AnswerResponse после ожидания очереди |
| POST | `/answer-jobs/` | QuestionRequest | 202 AnswerJobResponse |
| GET | `/answer-jobs/` | Bearer | 200 массив AnswerJobResponse |
| POST | `/answer-jobs/{turn_identifier}/stop/` | UUID turn | 204 |
| GET | `/health/` | Без авторизации | 200 HealthResponse или 503 unavailable |

Upload 202 подтверждает регистрацию задачи, не готовность. Dedup может вернуть существующий ready/failed/deleting документ. Chunk доступен только для ready документа. Original PDF не требует ready, но запрещён для deleting/deleted

## Входные схемы

| DTO | Поля и ограничения |
| --- | --- |
| CredentialsRequest | login_name: string 1-320 на HTTP-границе; password_text: string 12-128 |
| ProjectRequest | title_text: string 1-200; application проверяет strip и непустоту |
| ChatRequest | title_text: string 1-200 или null, default null; project_identifier: UUID или null, default null |
| IndexingRequest | document_identifier: UUID; rebuild: bool, default false |
| QuestionRequest | chat_identifier: UUID; client_request_identifier: UUID; question_text: string 1-4000 |

Регистрация дополнительно проверяет login_name: 3-64 Unicode-буквы/цифры, точка, дефис, underscore; strip/lower. Вход допускает старый адрес как логин до 320 символов. Пароль не нормализуется как логин. Ошибки ввода на application-границе возвращают стабильные коды

Чат без title_text автоматически называется по первому вопросу. Ручное название отключает automatic_title. IndexingRequest без rebuild повторяет failed; rebuild=true также разрешает ready. queued/indexing возвращают текущее состояние, deleting даёт 409, deleted - 404

## Ответы и цитаты

| DTO | Поля |
| --- | --- |
| AuthResponse | access_token, expires_in, token_type |
| IdentityResponse | user_identifier, login_name |
| ProjectResponse | project_identifier, title_text, created_at, updated_at |
| ChatResponse | chat_identifier, project_identifier, title_text, created_at, updated_at |
| DocumentResponse | document_identifier, project_identifier, filename_text, media_type, byte_count, state_name, chunk_count, indexed_chunk_count, indexing_progress_percent, page_count, embedding_profile, error_code, error_message, created_at, updated_at |
| ChunkResponse | chunk_identifier, document_identifier, ordinal_value, content_text, token_count, page_start, page_end, filename_text |
| CitationResponse | source_label, chunk_identifier, document_identifier, filename_text, page_start, page_end, excerpt_text, similarity_score, source_url |
| MessageResponse | message_identifier, chat_identifier, turn_identifier, role_name, content_text, citation_records, sufficient_context, turn_status, created_at, turn_error_code, response_duration_ms |
| AnswerResponse | turn_identifier, chat_identifier, user_message, assistant_message |
| AnswerJobResponse | turn_identifier, chat_identifier, project_identifier, status_name |
| HealthResponse | status_name: ready или unavailable |
| ErrorResponse | error_code, message_text |

DocumentState: queued/indexing/ready/failed/deleting/deleted. TurnStatus: queued/pending/completed/failed. Message role: user/assistant. Страницы PDF 1-based, ordinal фрагмента 0-based; у текстовых файлов страницы null

sufficient_context=true означает ответ по переданным свидетельствам с citations; false означает общий ответ без источников проекта, не обязательный отказ. User message имеет sufficient_context=null. Обзор без новых подтверждённых пунктов может иметь true и ссылку на проверенный раздел: это сообщение о результате проверки материалов

Метку `Sx` назначает сервер по позиции источника, она не обязана идти подряд среди использованных citations и не ограничена S1-S6. Context максимум 48 sources; default обзор выбирает до 40. source_url - защищённый относительный chunk endpoint, не публичная ссылка

similarity_score содержит cosine в векторной ветке. Для chunks обзорного SQL-отбора сохраняется 0.0: это не cosine-подтверждение низкой релевантности и не внутренний лексический ranking score

excerpt_text сохраняет до 1000 символов исходного chunk. После удаления/переиндексации snapshot не переписывается. Chunk по тому же ordinal после rebuild может иметь другой текст; snapshot сохраняет старую цитату

turn_error_code передаёт причину failed turn в истории, без ложного assistant success. response_duration_ms - nullable integer >= 0, измерение подготовки после выхода из очереди до commit; user/старые ответы имеют null. Completed replay возвращает прежнее время

Storage key, password hash, lease и tenant IDs внутренних записей не раскрываются в Document/Chat/Chunk responses. IdentityResponse намеренно содержит ID самого текущего пользователя

## Ошибки

| HTTP | Условия и примеры кодов |
| --- | --- |
| 400 | Некорректный Content-Length: invalid_content_length |
| 401 | authentication_required, invalid_access_token, invalid_credentials |
| 404 | project_not_found, chat_not_found, document_not_found, chunk_not_found, turn_not_found, stored_file_not_found |
| 409 | login_already_registered, chat_busy, request_question_mismatch, document_not_retryable, lease_lost |
| 413 | document_too_large, upload_too_large, agent_request_too_large |
| 415 | unsupported_document_format, unsupported_agent_content_type |
| 422 | invalid_request, invalid_login_name, invalid_question, invalid_utf8, invalid_pdf, encrypted_pdf, no_extractable_text, document_not_pdf и другие InvalidInputError |
| 503 | database_unavailable, model_unavailable, model_request_timeout, answer_timeout, model_response_truncated, invalid_search_plan, invalid_evidence_review и другие DependencyUnavailableError |

Схемная ошибка FastAPI нормализуется в invalid_request. DomainError map задаётся [main.py](../src/study_rag/main.py), upload preflight - [upload_limit.py](../src/study_rag/api/upload_limit.py). HTTP/SSE ошибка не доказывает отсутствие уже принятой задачи; состояние восстанавливается из БД

Stop отображается как failed/answer_stopped в history. Синхронный ожидающий /messages/ возвращает 503 с этим кодом, если задача была остановлена. Ошибки не запускают неограниченных повторов

## Оригинальный PDF

Original endpoint отдаёт полный файл без Range-поддержки: application/pdf, inline Content-Disposition с filename*=UTF-8, Cache-Control private/no-store, X-Content-Type-Options nosniff. X-PDF-Page-Count присутствует при известном числе страниц

Владелец проверяется до чтения файла. Endpoint не отдаёт TXT/MD и не рендерит PNG отдельной страницы. Браузер создаёт blob URL для viewer. Подробности: [pdf-sources.md](pdf-sources.md)

## AG-UI

`POST /rpc/study-rag/agent/` требует Bearer, application/json и поддерживает SSE через официальный AGUIAdapter. GUI в обычном режиме использует REST answer-jobs; `frontend/src/ag-ui.ts` сохранён для интеграционного транспорта

| Поле RunAgentInput | Допустимое значение |
| --- | --- |
| threadId | UUID существующего чата владельца |
| runId | UUID client_request_identifier |
| messages | 1-100 сообщений; последнее - user со строковым content и непустым id |
| Последний content | После strip 1-4000 символов |
| tools, context | Пустые массивы |
| state, forwardedProps | Пустой объект либо null |
| resume | Отсутствует или пустое значение |
| parentRunId | Отсутствует или null |
| protocolVersion | Отсутствует либо строка 1.0; единственное разрешённое extra поле |

Неподдерживаемые поля/возможности дают 422 invalid_agent_request. Body ограничен 64 KiB, включая поток без Content-Length; превышение 413. Неверный Content-Type - 415. Авторизация и доступ к чату проверяются до открытия SSE

Клиентская история не авторитетна: adapter получает только проверенный последний вопрос, сервер читает history из PostgreSQL. Bridge ждёт AnswerQueue, дополнительной LLM над результатом нет

Успешный lifecycle: RUN_STARTED, TEXT_MESSAGE_START, TEXT_MESSAGE_CONTENT, TEXT_MESSAGE_END, RUN_FINISHED. Отдаются готовый сохранённый текст и SDK events, не токены Ollama. Источники и постоянные ID читаются через REST /messages/

Ошибки после открытия SSE - RUN_ERROR с безопасным code/message; до SSE - обычный HTTP ErrorResponse. Headers: Cache-Control no-cache, X-Accel-Buffering no. Разрыв SSE прекращает ожидание, а не принятую задачу

## Внутренние контракты

Авторитетные типы: [entities.py](../src/study_rag/domain/entities.py); точные async-сигнатуры: [ports.py](../src/study_rag/domain/ports.py). Не следует копировать их в отдельные несовместимые DTO

| Port | Назначение |
| --- | --- |
| AuthRepository | Аккаунты и поиск нормализованного логина |
| ProjectRepository | Создание, выбор, список, переименование, удаление проектов |
| DocumentRepository | Регистрация, списки/ready chunks, rebuild/retry, claim/lease, публикация/очистка |
| ConversationRepository | Чаты, история, begin/complete/fail turn, claim_queued_turn, active/stop |
| PasswordSecurity / AccessTokenCodec | Argon2 и JWT |
| FileStorage | Атомарная загрузка, чтение/удаление по проверенному ключу |
| DocumentParser / TextTokenizer | Sections, page-aware chunks, token budgets |
| EmbeddingProvider / VectorStore | Embeddings, profile, filters и производный индекс |
| AnswerGenerator | SearchPlan, EvidenceItem, rewrite, structured/general answer, readiness |

DocumentContent содержит filename_text/media_type/content_bytes/page_count. SearchPlan и EvidenceItem с semantic_category/alias_names - внутренние записи, отдельной HTTP-схемы каталога нет

Production вопросы проходят AnswerQueue; ConversationService.execute_reserved_turn исполняет захваченный turn. Прямой ConversationService.answer_question остаётся внутренним сценарием и используется тестами, но HTTP routes делегируют очередь

[Состояния и восстановление](answer-queue.md), [изоляция](projects-and-math.md), [поиск](adaptive-search.md), [настройки](configuration.md)

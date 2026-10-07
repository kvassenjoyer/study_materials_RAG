# Архитектура GUI Mini-RAG

Статус: реализовано и проверено 4 октября 2026 года;
[результаты автоматических проверок и браузерного smoke](gui-validation.md).
Первичная версия этой архитектуры создана до реализации GUI.
Пользователь разрешил следующий этап: настоящий графический интерфейс с
[официальной интеграцией Pydantic AI AG-UI](https://pydantic.dev/docs/ai/integrations/ui/ag-ui/).
Этот документ расширяет [архитектуру бэкенда](architecture.md) и опирается на
[существующие контракты](contracts.md), `api/routes.py`, `api/schemas.py` и
`application/conversations.py`. Предыдущее ограничение «GUI отложен» относилось
к завершённому этапу API; текущий этап включает UI.

## Решение

Небольшое приложение React + TypeScript + Vite в `frontend/`. Собранный
`src/study_rag/web/` отдаёт существующий FastAPI на `http://127.0.0.1:8000/`.
Swagger остаётся на `/docs`, OpenAPI на `/openapi.json`; REST API сохраняет
префикс `/rest/study-rag`. В разработке Vite проксирует `/rest` и `/rpc` в FastAPI,
поэтому JWT и API работают с одним origin без CORS-обходов.

AG-UI используется для отправки вопроса и событий выполнения. React вызывает
официальный `HttpAgent` из `@ag-ui/client`; сервер действительно запускает
`pydantic_ai.ui.ag_ui.AGUIAdapter`, а не самодельный SSE под этим названием.
Pydantic AI Agent получает локальный FunctionModel bridge,
который делегирует запрос существующему `ConversationService.answer_question`.
История, retrieval, Qwen, проверка citations и транзакции остаются в этом сценарии.
Новый облачный провайдер и второй RAG pipeline не нужны.

```text
React UI ── HTTP Bearer REST ── FastAPI ── application scenarios
        └── @ag-ui/client ── AGUIAdapter ── Pydantic AI Agent
                                               └── model bridge
                                                    └── ConversationService
                                                          ├── PostgreSQL
                                                          ├── Qdrant
                                                          └── local Ollama
```

PostgreSQL — источник истины для переписки. AG-UI messages/state в браузере
служат транспортом и временным представлением текущего run; они не заменяют
`fetch_history`, не определяют tenant и не перезаписывают сообщения в БД.
Существующий нестриминговый Ollama возвращает готовый ответ целиком. SSE
показывает lifecycle run и итоговый текст; UI не обещает поток отдельных токенов.

## Экран и поведение

Интерфейс на русском, ответы и материалы поддерживают русский и английский.
Один рабочий экран: боковая панель чатов и материалов, основная переписка,
поле вопроса и карточки источников под ответом. На узком экране боковая панель
открывается кнопкой; формы и composer остаются доступны с клавиатуры.

- Вход / регистрация: email, пароль 12–128 символов, понятные ошибки,
  переключение формы; успешный ответ сразу открывает рабочий экран.
- Чаты: список с датой, «Новый чат», выбор предыдущего чата, загрузка истории
  из PostgreSQL. Пустой чат объясняет, что можно загрузить и спросить.
- Материалы: выбор или drop TXT/MD/PDF, имя и размер файла, состояния
  «В очереди», «Индексируется», «Готов», «Ошибка», «Удаляется».
  `failed` показывает безопасную причину и кнопку повторной индексации;
  удаление отдельного материала требует короткого подтверждения в UI.
- Вопрос: Enter отправляет, Shift+Enter создаёт строку; максимум 4000 символов.
  Во время run composer блокирует повторную отправку в этот чат и показывает
  «Ищу ответ в материалах…». Отказ при недостатке контекста отображается
  обычным сохранённым ответом, без выдуманных источников.
- Источники: метка S1…S6, имя файла, страницы при наличии, excerpt и действие
  «Открыть фрагмент». Источник открывается в защищённой панели внутри UI.
- Верхняя панель: текущий email, выход, ссылка на `/docs`.

Состояния загрузки, ошибки сети, пустые списки, expired session и отсутствие
готовых материалов имеют отдельное видимое объяснение. UI не показывает
внутренние промпты, модели и lease как часть основного пользовательского потока.

## Авторизация и данные браузера

JWT хранится в `sessionStorage` и памяти вкладки. Каждый REST и AG-UI запрос
получает `Authorization: Bearer <token>` в header. Пароль не сохраняется.
При запуске вкладки `/users/current/` проверяет восстановленный токен.
401 очищает JWT, выбранный чат, временные сообщения, источники и кэш текущего
пользователя; выход делает то же самое. Отмена активного HTTP/SSE запроса
при выходе предотвращает применение позднего ответа в другую сессию.
Refresh tokens отсутствуют: после истечения JWT пользователь входит снова.

Tenant извлекается только из проверенного JWT на сервере. Browser state,
`threadId`, `forwardedProps`, `context` и message history не являются
авторизацией. Любой чужой UUID проверяется сценариями/repositories и даёт 404.
Токен никогда не попадает в URL, query string, логи или ссылки на источники.

Ответ и Markdown показываются безопасным renderer без raw HTML. Внешние
ссылки допускают только безопасные web-схемы; citation action использует
same-origin authenticated fetch к `/chunks/{uuid}/`, а не `window.open`
с токеном в URL. Для исторической citation после удаления документа остаётся
сохранённый excerpt; 404 сообщает, что полный фрагмент больше недоступен.

## REST контракты UI

Все пути ниже относительно `/rest/study-rag`, с завершающим `/`.
Поля соответствуют текущим Pydantic schemas, без переименования backend DTO.

| Действие | Запрос | Результат / существенные поля |
| --- | --- | --- |
| Регистрация | `POST /users/`, `{email_address, password_text}` | 201: `{access_token, expires_in, token_type}` |
| Вход | `POST /sessions/`, то же тело | 200: AuthResponse |
| Текущий пользователь | `GET /users/current/` | `{user_identifier, email_address}` |
| Материалы | `GET /documents/?limit=50&offset=0` | `{items, total_count, offset, limit}` |
| Загрузка | `POST /documents/`, multipart field `file` | 202: DocumentResponse; до 100 MiB |
| Статус | `GET /documents/{document_identifier}/` | DocumentResponse |
| Повтор индексации | `POST /indexing-jobs/`, `{document_identifier}` | 202: DocumentResponse |
| Удаление | `DELETE /documents/{document_identifier}/` | 202: `state_name=deleting`; очистка фоном |
| Полный фрагмент | `GET /chunks/{chunk_identifier}/` | ChunkResponse с `content_text`, filename, pages |
| Список чатов | `GET /chats/?limit=50&offset=0` | PageResponse<ChatResponse> |
| Создать чат | `POST /chats/`, `{title_text}` | 201: ChatResponse; default «Новый чат» |
| Проверить чат | `GET /chats/{chat_identifier}/` | ChatResponse |
| История | `GET /messages/?chat_identifier={uuid}&limit=100&offset=0` | PageResponse<MessageResponse>, chronological |
| Совместимый обычный вопрос | `POST /messages/`, `{chat_identifier, client_request_identifier, question_text}` | AnswerResponse; UI использует AG-UI |

DocumentResponse: `document_identifier`, `filename_text`, `media_type`,
`byte_count`, `state_name`, `chunk_count`, `page_count`, `embedding_profile`,
`error_code`, `error_message`, `created_at`, `updated_at`.
MessageResponse: `message_identifier`, `chat_identifier`, `turn_identifier`,
`role_name`, `content_text`, `citation_records`, `sufficient_context`,
`turn_status`, `created_at`. CitationResponse: `source_label`,
`chunk_identifier`, `document_identifier`, `filename_text`, `page_start`,
`page_end`, `excerpt_text`, `similarity_score`, `source_url`.

Историю UI дочитывает страницами до `total_count`; порядок на сервере
chronological. `pending` / `failed` user rows сохраняются и отображаются
с их состоянием, ложный assistant success не создаётся. Список материалов
периодически обновляется только при `queued`, `indexing` или `deleting`;
poll прекращается после готовности/ошибки и при выходе.

Обычная ошибка API: `{error_code, message_text}`. UI переводит известные
коды в русский текст и сохраняет безопасный fallback. 401 — вход заново,
404 — ресурс недоступен, 409 — чат занят / состояние изменилось,
413 — слишком большой файл, 415 — неподдерживаемый формат,
422 — неверные поля, 503 — зависимость недоступна. Ошибки не стирают
успешно сохранённую переписку и не запускают бесконечный retry.

## AG-UI контракт

Новый защищённый endpoint: `POST /rpc/study-rag/agent/`.
Content-Type `application/json`, Accept `text/event-stream`, Bearer JWT.
Тело — официальный `RunAgentInput`:

- `threadId`: UUID существующего чата.
- `runId`: UUID запроса, совпадает с `client_request_identifier` сценария.
- `messages`: до 100 сообщений; последняя user message содержит `id`,
  `role: "user"`, строковый `content` (1–4000 символов). Клиентский transcript не используется как
  авторитетная история; сервер выбирает текущий вопрос и передаёт только его
  bridge, а историю читает ConversationService.
- `tools: []`, `context: []`, `state: {}`, `forwardedProps: {}`.
  Непустые tools, context, state, forwardedProps, а также resume отклоняются:
  этот UI не поддерживает выполнение browser tools и внешних instructions.
- Официальный JS client `1.0.1` добавляет `protocolVersion: "1.0"`.
  Это единственное допустимое extra поле: другие extras и любая другая версия
  возвращают 422. Поле может отсутствовать; `parentRunId` отсутствует или `null`.

Тело ограничено 64 KiB. Сервер до открытия SSE проверяет токен, UUID,
доступ к чату и формат вопроса.
После этого официальный AGUIAdapter преобразует Agent events в AG-UI и
кодирует SSE. Bridge захватывает только server-authenticated identity,
chat UUID, run UUID, вопрос и существующий ConversationService.
Модель bridge не вызывает внешний LLM второй раз: `FunctionModel` с async
`stream_function` один раз ждёт сценарий и выдаёт итоговый сохранённый
`assistant_message.content_text`. Agent и adapter выполняют только эту
транспортную обёртку; текущий Ollama RAG остаётся внутри ConversationService.

События успешного run: `RUN_STARTED`, `TEXT_MESSAGE_START`,
`TEXT_MESSAGE_CONTENT` с итоговым ответом, `TEXT_MESSAGE_END`, `RUN_FINISHED`.
Точная генерация text lifecycle поручена
официальному adapter; количество content events не обещает token streaming.
После `RUN_FINISHED` UI перечитывает HTTP history и заменяет временный run
сохранёнными MessageResponse, чтобы ID, citations и статусы совпадали с БД.
`citation_records` и `sufficient_context` сохранённого assistant message
дают источники и состояние grounded answer без парсинга ссылок из Markdown.
Отдельный custom/state event для citations первой версии не требуется.
При ошибке или потере соединения UI также перечитывает историю; ошибка во
время SSE выражается `RUN_ERROR`, до SSE — обычным ErrorResponse с HTTP status.
Кнопка «Повторить» сохраняет исходный UUID run и исходный вопрос: completed
request replay возвращает тот же ответ, failed request безопасно повторяется
без второго user message. Новый вопрос получает новый UUID.

Разрыв SSE не доказывает откат БД. Клиент не делает автоматическую повторную
отправку с новым UUID. Новый transport run не меняет PostgreSQL turn semantics.
Переключение чата не должно переносить временные сообщения или источники;
callback проверяет session и chat перед применением результата.

## Файлы, зависимости и запуск

| Область | Файлы | Ответственность |
| --- | --- | --- |
| UI | `frontend/src/{App,main}.tsx`, components, styles | Auth, материалы, чаты, источники, responsive layout |
| REST boundary | `frontend/src/api.ts`, `types.ts` | Typed DTO, JWT header, ошибки, pagination |
| AG-UI boundary | `frontend/src/ag-ui.ts` | HttpAgent lifecycle и чтение history после completion |
| Build | `frontend/{package.json,package-lock.json,tsconfig.json,vite.config.ts,index.html}` | Vite, proxy, reproducible npm dependencies |
| AG-UI backend | `src/study_rag/api/ag_ui.py`, включая model bridge | Auth + adapter + делегирование сценария |
| Static host | `main.py`, `api/frontend.py` | `/`, `/assets`, `/favicon.svg`; API/docs не перекрываются |

JS production зависимости: React, React DOM, `@ag-ui/client`,
`react-markdown`, `remark-gfm`; иконки реализованы локальными SVG.
Tooling: TypeScript, Vite, React Vite plugin, типы React/React DOM; tests — Vitest.
Точные версии зафиксированы в package lock.
Python: `pydantic-ai-slim[ag-ui]==2.54.0` (проверенный выпуск PyPI) и
транзитивный `ag-ui-protocol==0.1.22` (в extra допустим `>=0.1.10,<1`);
существующий FastAPI/Starlette используется повторно.
JS client `1.0.1` использует более новую версию протокола: совместимость
узкого набора lifecycle/text событий с Python `0.1.22` подтверждена transport
tests и настоящим браузерным smoke. Сервер принимает его marker `protocolVersion: "1.0"`.
Не устанавливать полный набор облачных SDK, если нужен только local bridge.

Production: `npm ci`, `npm run build` в frontend, затем существующий запуск
FastAPI на 8000. Vite `outDir` — `../src/study_rag/web`; static mount разрешает
этот каталог относительно Python `__file__`, чтобы работали checkout и wheel.
Сгенерированный каталог игнорируется Git; Hatch artifacts явно включают
собранные web files в wheel, что проверяется просмотром содержимого wheel.
Docker получает Node build stage и копирует assets перед `uv sync`, поэтому
production image содержит готовый GUI. Без собранного UI `/` возвращает
полезную HTML-страницу с командой сборки; API продолжает работать.
Сборка не выполняется внутри lifespan. Dev: backend на 8000 и Vite на 5173
с proxy `/rest` и `/rpc` → 8000.
Нет нового сервера данных, контейнера или порта моделей. Native PostgreSQL
остаётся на 55432; embedded Qdrant требует один процесс Uvicorn, Ollama локальный.

## Приёмка

1. TypeScript check и production build проходят; `/` отдаёт GUI, `/docs`
   остаётся Swagger, assets корректно загружаются с того же origin.
2. Через UI зарегистрировать/войти, загрузить TXT, MD и PDF; увидеть
   queued/indexing/ready, осмысленный failed, retry и удаление.
3. Создать чат, получить реальный локальный ответ с citations, задать
   уточнение, открыть источник; reload восстанавливает сообщения из PostgreSQL.
4. Два пользователя не видят материалы, историю и источники друг друга;
   401 очищает browser state и возвращает форму входа.
5. Запрос frontend проходит официальный HttpAgent → AGUIAdapter → Pydantic
   AI bridge → ConversationService; lifecycle имеет valid AG-UI types,
   citations и persisted message ID восстанавливаются через REST history.
6. Ошибка / disconnect / повтор с тем же run UUID не создаёт дубликаты;
   stale callbacks, unsafe Markdown и citation URL не обходят auth.
7. Keyboard и узкий viewport проверены; долгий локальный inference имеет
   понятное busy state. Реальный smoke отличён от fake model contract tests.

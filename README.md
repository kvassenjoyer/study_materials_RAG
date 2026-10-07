# Мини-RAG для поиска ответов по учебным материалам

Локальное приложение с браузерным интерфейсом: загрузка TXT, Markdown и PDF с текстовым
слоем, поиск по собственным материалам и краткие ответы со ссылками на фрагменты.
React + TypeScript работают поверх FastAPI; PostgreSQL хранит
пользователей, документы и чаты, Qdrant — векторный индекс. Русский и английский поддерживаются
одними моделями. Пользователи и их история изолированы авторизацией.

Откройте [приложение](http://127.0.0.1:8000/) после запуска по инструкции ниже.
Создайте аккаунт или войдите, добавьте материалы, дождитесь состояния «Готов»
и задайте вопрос в чате. Карточки источников позволяют открыть точный фрагмент.
[Swagger](http://127.0.0.1:8000/docs) остаётся доступен для работы с API.

Архитектура создана до реализации отдельным агентом: [бэкенд](docs/architecture.md),
[интерфейс и AG-UI](docs/frontend-architecture.md),
[контракты](docs/contracts.md), [требования и ответы пользователя](docs/requirements.md).
Разработка разделена между архитектурой, сценариями/API, persistence и model adapters.
Правила Python основаны на предоставленном архиве: небольшие классы, явный DI,
строгие типы и последовательные сценарии. Локальные `.agents/` и `.local/` исключены из Git.

## Модели

По умолчанию обе модели работают через Ollama на localhost:

```bash
ollama pull embeddinggemma:300m
ollama pull qwen3.5:4b
```

Ollama должен быть запущен по адресу `http://127.0.0.1:11434`. Сервис использует
EmbeddingGemma для 768-мерных embeddings и Qwen3.5-4B для rewrite вопросов и ответов.
Облачного инференса и вызовов OpenRouter нет. Первое скачивание весов и HF tokenizer
требует интернет; последующие вызовы моделей выполняются локально.

Hugging Face используется для публичного tokenizer `Qwen/Qwen3.5-4B`. В default режиме
`token_count` относится к этому tokenizer, а не к закрытому tokenizer Gemma: дополнительно
применяется консервативный byte budget, и Ollama запрещено молча обрезать embedding input.
Query/document prefixes передаются явно. Это ограничение default режима зафиксировано
в profile индекса, а не скрыто от пользователя.

Полноценный opt-in HF backend `google/embeddinggemma-300m` поддержан через Sentence
Transformers с собственным tokenizer. Требуются принятие [лицензии модели](https://huggingface.co/google/embeddinggemma-300m)
и авторизация Hugging Face:

```bash
uv sync --extra huggingface
uv run --no-sync hf auth login
```

В `.env` установите `STUDY_RAG_EMBEDDING_BACKEND=huggingface`, при необходимости
`STUDY_RAG_EMBEDDING_DEVICE=mps`. Используется float32; fp16 не поддерживается моделью.
`auto` выбирает доступное устройство. При смене backend/tokenizer/chunk settings меняется
embedding profile: повторно загрузите материалы. Одинаковый файл в новом profile получает
новый UUID, а embeddings разных profiles не смешиваются. Старые документы можно удалить.

## Запуск

Требуются Python 3.12, [uv](https://docs.astral.sh/uv/getting-started/installation/)
и Node.js 26 с npm для сборки интерфейса (минимум Node.js 22.12).

```bash
npm --prefix frontend ci
npm --prefix frontend run build
uv sync --locked
cp -n .env.example .env
```

Vite собирает интерфейс в `src/study_rag/web`; FastAPI отдаёт эти файлы с того же
origin, что и API. Повторяйте сборку после изменения frontend. В контейнерном варианте
Node stage Dockerfile выполняет сборку самостоятельно.

Укажите в `.env` доступный PostgreSQL URL и случайный JWT secret минимум 32 байта.
Пустой secret из примера намеренно не позволяет запустить приложение. Не публикуйте `.env`.
Для первого создания секрета подходит `openssl rand -hex 32`; сохраните результат в
`STUDY_RAG_JWT_SECRET`. Существующий `.env` командой `cp -n` не заменяется.

Native вариант: PostgreSQL работает локально, Qdrant сохраняется в `.data/qdrant` через
embedded qdrant-client, Docker не нужен. В этом окружении подготовлен PostgreSQL 17
с cluster внутри `.local/` и портом 55432; рабочий URL уже задан в локальном `.env`.
Для чистой установки нужен собственный PostgreSQL, отдельный project cluster или Compose.

```bash
uv run alembic upgrade head
uv run uvicorn study_rag.main:create_application --factory --host 127.0.0.1 --port 8000 --workers 1
```

Откройте [интерфейс](http://127.0.0.1:8000/).
[Swagger](http://127.0.0.1:8000/docs) доступен отдельно. Если API уже работает
без `--reload`, после обновления кода и сборки остановите его через `Ctrl+C`
в прежнем терминале и снова выполните ту же команду `uv run uvicorn` выше.
Второй процесс с тем же embedded Qdrant запускать нельзя. Проверка готовности:
[health](http://127.0.0.1:8000/rest/study-rag/health/). Первый старт скачивает tokenizer
в `.data/models`. Startup проверяет зависимости; отсутствие модели/БД не подменяется
фиктивным успешным ответом. Embedded Qdrant допускает только один процесс приложения.
При ошибке `Storage folder ... is already accessed` или предупреждении Hugging Face
см. [устранение ошибок запуска](docs/runbook.md#troubleshooting).

Если установлен Docker, можно поднять только зависимости:

```bash
docker compose up -d database qdrant
```

В `.env` используйте PostgreSQL порт 5432 из `.env.example` и задайте
`STUDY_RAG_QDRANT_URL=http://127.0.0.1:6333`. Ollama остаётся на хосте.
Dockerfile и необязательный профиль приложения доступны для контейнерного запуска:

```bash
docker compose --profile application build application
docker compose --profile application run --rm application uv run --no-sync alembic upgrade head
docker compose --profile application up -d application
```

Контейнер обращается к Ollama через `host.docker.internal`; сервер Ollama должен быть доступен
из контейнера. Порты Compose опубликованы только на localhost. Контейнерный путь нужно
проверять в окружении с Docker; наличие конфигурации не является результатом такой проверки.

## Использование в браузере

1. Откройте [приложение](http://127.0.0.1:8000/), выберите «Создать аккаунт»
   и задайте email и пароль из 12–128 символов. Для существующего аккаунта
   используйте вкладку «Войти».
2. В боковой панели добавьте TXT, Markdown или PDF с текстовым слоем. Для первого
   примера подходит [gradient-descent-ru.md](examples/materials/gradient-descent-ru.md).
   Дождитесь состояния «Готов»: загрузка запускает фоновую индексацию.
3. Нажмите «Новый чат», задайте название и нажмите «Создать». Спросите
   `Что такое градиентный спуск и как меняются параметры?`.
4. Под ответом откройте карточку источника через «Открыть фрагмент». Для PDF
   показываются страницы; источник загружается с авторизацией внутри интерфейса.
5. В том же чате спросите `Покажи численный пример из материала.`. История
   сохраняется в PostgreSQL и восстанавливается при выборе чата или обновлении страницы.

Enter отправляет вопрос, Shift+Enter добавляет строку; вопрос ограничен 4000 символами.
Во время ответа интерфейс показывает состояние ожидания. SSE через официальный
`@ag-ui/client` и Pydantic AI `AGUIAdapter` передаёт события выполнения и готовый
локальный ответ; отдельные токены Ollama не стримятся. Источники и постоянные ID
сообщений загружаются через REST после выполнения запроса.

Для материала со статусом «Ошибка» доступна повторная индексация; удаление
подтверждается в интерфейсе. История сохраняет excerpt источника после удаления
материала, но полный фрагмент больше недоступен. «Повторить запрос» после ошибки
использует прежний run UUID и вопрос, сохраняя idempotency.

JWT хранится в `sessionStorage` вкладки. Выход и ответ 401 очищают состояние
пользователя; после истечения токена нужно войти заново. Материалы, чаты и источники
каждого аккаунта доступны только его владельцу.

Для разработки интерфейса оставьте backend на 8000 и запустите:

```bash
npm --prefix frontend run dev
```

Откройте `http://127.0.0.1:5173/`: Vite проксирует `/rest` и `/rpc` в backend.
Для обычного использования достаточно production сборки и одного процесса FastAPI.

## Использование API через Swagger

Для вызова операции раскройте её, нажмите **Try it out**, заполните поля и нажмите
**Execute**. Ответ появится в **Response body**.

1. `POST /rest/study-rag/users/`: задайте `email_address`, `password_text` (12–128 символов).
   Регистрация возвращает JWT. Для повторного входа есть `POST /rest/study-rag/sessions/`.
2. Нажмите **Authorize**, вставьте `access_token`; Swagger добавит Bearer header.
3. `POST /rest/study-rag/documents/`: загрузите файл в поле `file`. Ответ 202 означает
   сохранённую задачу, а не завершённую индексацию. Следите через document GET за переходом
   `queued → indexing → ready`. Одинаковый файл в том же profile не индексируется повторно.
4. Создайте чат через `POST /rest/study-rag/chats/`. Отправьте в `POST /rest/study-rag/messages/`
   его UUID, новый `client_request_identifier` UUID и `question_text`.
5. Ответ находится в `assistant_message.content_text`. `citation_records` содержит явные
   `source_label` для `[S1]`, UUID документа/фрагмента, страницу PDF, excerpt и `source_url`.
   Ссылку на chunk открывайте с тем же Bearer; в Swagger используйте chunk GET.
6. Для уточнения отправляйте вопрос в тот же chat UUID. Список чатов и история доступны
   через chat GET и `GET /rest/study-rag/messages/?chat_identifier=...`.

Повтор завершённого `client_request_identifier` возвращает сохранённый ответ без новых
сообщений. Другой вопрос под тем же UUID и параллельный запрос активного чата получают 409.
Недостаточный контекст возвращает `sufficient_context=false` без citations. Отказ сохранён
в истории; ошибка зависимости возвращает 503 и failed turn, который можно повторить.

Ошибки индексации видны в `error_code`/`error_message`; retry выполняется через
`POST /rest/study-rag/indexing-jobs/` с document UUID. Delete сразу исключает документ
из поиска; worker затем очищает файл и vectors. История и citation snapshot сохраняются,
но удалённый chunk URL возвращает 404. Чужие UUID тоже дают 404.

## Ограничения и восстановление

Форматы: UTF-8 TXT/Markdown, PDF с текстовым слоем. Сканированные PDF, фотографии и рукописные
конспекты требуют OCR и отложены. По умолчанию upload до 100 MiB, PDF до 5000 страниц,
20 миллионов извлечённых символов и 100 тысяч chunks. Chunked request ограничивается
до multipart spooling, file payload проверяется отдельно. Ресурсы доступны только по Bearer.

Очередь хранится в PostgreSQL. После перезапуска queued/expired jobs продолжаются;
partial chunks и vectors не видны в поиске до ready. Короткие транзакции с lease/CAS,
детерминированные point UUID и idempotent upsert/delete обеспечивают повтор без duplicates.
Никакой транзакции между PostgreSQL и Qdrant не обещается. Локальный MVP рассчитан на
десятки книг и один worker, а не на горизонтально масштабированный production.

Начальный similarity threshold 0.35 настраивается через `STUDY_RAG_MINIMUM_SIMILARITY`.
Это не измеренная гарантия качества. Проверка labels исключает выдуманные ссылки,
но сама по себе не доказывает фактическую корректность каждого предложения.
GUI использует официальный AG-UI транспорт и Pydantic AI FunctionModel bridge
к существующему RAG-сценарию. OpenRouter остаётся требованием будущей версии;
инференс текущего приложения выполняется локально.

## Проверки

В текущей версии прошли **126 Python-тестов без skipped/failed** на отдельной
PostgreSQL test DB, Ruff ALL, форматирование, строгий mypy и COP.
Frontend: **11 Vitest-тестов**, TypeScript check и production Vite build прошли.
Настоящий browser smoke прошёл вход, Markdown upload → «Готов», чат,
ответ Ollama с источниками, численный follow-up, открытие фрагмента,
reload истории и повторный вход; проверены мобильное меню и web assets в wheel.
Подробные результаты и границы: [gui-validation.md](docs/gui-validation.md).
На завершённом этапе backend настоящий HTTP smoke с EmbeddingGemma и
Qwen прошёл RU/EN вопросы, численный follow-up `3.2`, citations, историю, replay,
изоляцию пользователей, отказ без контекста и удаление. Отдельно проверены TXT,
текстовый PDF со ссылкой на страницу и сохранение данных после перезапуска API.
Команды и границы той проверки: [backend-validation.md](docs/backend-validation.md).
Полная инструкция запуска: [runbook.md](docs/runbook.md), CI: [ci.md](docs/ci.md).

```bash
npm --prefix frontend test
npm --prefix frontend run build
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run flake8 src tests scripts
uv run pytest -q
```

Тесты модели в обычном pytest используют fake HTTP на инфраструктурной границе.
PostgreSQL integration tests требуют отдельной тестовой БД, заданной через
`TEST_DATABASE_URL`; без неё эти проверки помечаются skipped. Не указывайте
рабочую БД: integration fixture управляет собственными тестовыми данными/схемой.
Тесты запускаются последовательно до появления изоляции ресурсов для xdist.

Отдельный настоящий smoke выполняется против запущенного сервиса:

```bash
uv run python scripts/smoke_local.py
```

Он создаёт отдельные временные аккаунты и чаты, загружает собственные RU/EN fixtures,
проверяет содержательный численный follow-up, citations, историю, replay, tenant isolation,
отказ без знания и удаление. Загруженные документы очищаются; созданные пустые аккаунты
и история остаются, поскольку API удаления пользователя в MVP отсутствует. Этот smoke
использует реальные локальные модели; обычный pytest не заменяет его.

В `examples/materials/` находятся маленькие демонстрационные материалы, а
`examples/evaluation.jsonl` задаёт образец корпуса для последующей оценки. Пользовательского
корпуса пока нет; показатели accuracy/recall не заявляются. Подготовьте RU/EN вопросы,
ожидаемые документы/страницы, необходимые факты и вопросы без ответа в материалах.

Ruff использует ALL с пояснёнными исключениями, mypy — strict. В COP plugin отключены
несовпадающие с архивом требования verb whitelist, one_ prefixes, внешнего Mutable marker
и запрет полезных промежуточных результатов. Остальные правила сохранены; исключения
на внешних field/framework именах локальны и комментируются.

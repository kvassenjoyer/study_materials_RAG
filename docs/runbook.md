# Запуск и проверка Mini-RAG

Backend принимает UTF-8 TXT, Markdown и PDF с текстовым слоем, хранит материалы и
историю отдельно для каждого пользователя. Браузерный интерфейс React + TypeScript
доступен на `/`, Swagger — на `/docs`. Вопросы проходят официальный AG-UI транспорт
Pydantic AI к существующему локальному RAG. Контракты запросов описаны в
[contracts.md](contracts.md), проверенные модели — в [local-models.md](local-models.md),
результаты проверок — в [backend-validation.md](backend-validation.md).
GUI и настоящий браузерный smoke — в [gui-validation.md](gui-validation.md).

Все команды выполняются из корня репозитория. Выберите один вариант PostgreSQL/Qdrant:
Compose или native macOS. Ollama в обоих вариантах работает на хосте.

## Зависимости и локальные модели

Нужны Python 3.12, `uv`, Node.js 26 и npm; frontend требует минимум Node.js 22.12.
На macOS `uv` можно установить через `brew install uv`.
Зависимости и интерфейс устанавливаются из lock-файлов:

```sh
npm --prefix frontend ci
npm --prefix frontend run build
uv python install 3.12
uv sync --locked --group dev --python 3.12
```

Сборка включает TypeScript check и Vite build; результат лежит в
`src/study_rag/web`. FastAPI отдаёт `/`, `/assets/` и `/favicon.svg` с того же
origin. Необработанный checkout без сборки показывает на `/` инструкцию сборки.
Обновление frontend требует повторить `npm --prefix frontend run build`.

Запустите установленное приложение Ollama. Если daemon не запущен, в отдельном
терминале выполните `ollama serve`. Затем скачайте точные модели:

```sh
ollama pull embeddinggemma:300m
ollama pull qwen3.5:4b
ollama list
```

По умолчанию приложение обращается к `http://127.0.0.1:11434`. Оно не запускает
daemon и не скачивает Ollama-веса самостоятельно. Первый старт также скачивает
публичный Hugging Face tokenizer `Qwen/Qwen3.5-4B` в `.data/models`.

## Создание `.env`

Этот скрипт создаёт новый `.env` из [.env.example](../.env.example), генерирует JWT
secret из 32 случайных байт, задаёт файлу права `0600` и ничего не печатает.
Если `.env` уже существует, он прекращает работу без чтения или замены файла.

```sh
uv run --no-sync python - <<'PY'
import os
import pathlib
import secrets

destination = pathlib.Path(".env")
template = pathlib.Path(".env.example").read_text(encoding="utf-8")
configuration = template.replace(
    "STUDY_RAG_JWT_SECRET=\n",
    "STUDY_RAG_JWT_SECRET=" + secrets.token_hex(32) + "\n",
    1,
)
try:
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit(".env уже существует; он не изменён.") from None
with os.fdopen(descriptor, "w", encoding="utf-8") as environment_file:
    environment_file.write(configuration)
PY
```

Редактируйте адреса зависимостей в `.env` локальным редактором. Сохраните созданный
JWT secret: его смена делает ранее выданные токены недействительными. Настройки из
окружения процесса имеют приоритет над `.env`.

## Вариант 1: PostgreSQL и Qdrant через Compose

Нужны Docker и Docker Compose. Команда поднимает PostgreSQL 17 и Qdrant; API
запускается на хосте тем же Python окружением:

```sh
docker compose up -d database qdrant
docker compose ps
```

В `.env` задайте следующие адреса. PostgreSQL credentials ниже — локальные значения
из [compose.yaml](../compose.yaml):

```dotenv
STUDY_RAG_DATABASE_URL=postgresql+asyncpg://study_rag:study_rag@127.0.0.1:5432/study_rag
STUDY_RAG_QDRANT_URL=http://127.0.0.1:6333
STUDY_RAG_OLLAMA_URL=http://127.0.0.1:11434
```

Порты PostgreSQL/Qdrant опубликованы на `127.0.0.1`. Дождитесь состояния `healthy`
у `database`, затем переходите к миграции и старту API ниже.

## Вариант 2: native macOS, PostgreSQL 17 и embedded Qdrant

Установите PostgreSQL 17 через Homebrew:

```sh
brew install postgresql@17
```

Следующие команды создают отдельный project cluster внутри `.local/postgres`
и запускают его на `127.0.0.1:55432`. Выполняйте `initdb` и `createdb` только при
первом создании этого cluster. Глобальный Homebrew service запускать не требуется.

```sh
STUDY_RAG_PG_BIN="$(brew --prefix postgresql@17)/bin"
mkdir -p .local/postgres-socket
"$STUDY_RAG_PG_BIN/initdb" -D "$PWD/.local/postgres" \
  --username=study_rag --encoding=UTF8 --locale=C \
  --auth-local=trust --auth-host=trust
"$STUDY_RAG_PG_BIN/pg_ctl" -D "$PWD/.local/postgres" \
  -l "$PWD/.local/postgres.log" \
  -o "-h 127.0.0.1 -p 55432 -k \"$PWD/.local/postgres-socket\"" start
"$STUDY_RAG_PG_BIN/createdb" -h 127.0.0.1 -p 55432 -U study_rag study_rag
```

`trust` используется для отдельного localhost cluster на персональной машине.
В `.env` задайте:

```dotenv
STUDY_RAG_DATABASE_URL=postgresql+asyncpg://study_rag@127.0.0.1:55432/study_rag
STUDY_RAG_QDRANT_DIRECTORY=.data/qdrant
STUDY_RAG_OLLAMA_URL=http://127.0.0.1:11434
```

Переменная `STUDY_RAG_QDRANT_URL` должна отсутствовать и в `.env`, и в окружении
процесса. Для текущего shell её можно удалить командой `unset STUDY_RAG_QDRANT_URL`.
Embedded Qdrant хранит индекс в `.data/qdrant` и допускает один процесс приложения.

После остановки cluster повторный запуск использует существующие данные:

```sh
STUDY_RAG_PG_BIN="$(brew --prefix postgresql@17)/bin"
"$STUDY_RAG_PG_BIN/pg_ctl" -D "$PWD/.local/postgres" \
  -l "$PWD/.local/postgres.log" \
  -o "-h 127.0.0.1 -p 55432 -k \"$PWD/.local/postgres-socket\"" start
```

## Миграция и старт API

Выполняйте миграции перед запуском приложения, в том числе после обновления
схемы. Startup проверяет схему PostgreSQL и не создаёт её автоматически.

```sh
uv run --no-sync alembic upgrade head
uv run --no-sync uvicorn study_rag.main:create_application \
  --factory --host 127.0.0.1 --port 8000 --workers 1
```

Откройте [приложение](http://127.0.0.1:8000/). Регистрация, загрузка материалов,
чаты и источники доступны в браузере. [Swagger](http://127.0.0.1:8000/docs)
сохраняется для прямых запросов API. Готовность можно проверить так:

```sh
curl --fail http://127.0.0.1:8000/rest/study-rag/health/
```

Успешный ответ — `{"status_name":"ready"}`. Проверяются PostgreSQL, Qdrant и
модели; при недоступной зависимости возможен 503 или отказ startup. Первый старт
с загрузкой tokenizer занимает больше времени, чем запуск с готовым cache.

Необязательный профиль `application` в Compose запускает приложение с готовым GUI:

```sh
docker compose --profile application build application
docker compose --profile application run --rm application uv run --no-sync alembic upgrade head
docker compose --profile application up -d application
```

Dockerfile собирает frontend в отдельном Node.js 26 stage и включает web assets
в Python image. В этом профиле адреса PostgreSQL/Qdrant задаёт Compose, Ollama доступен через
`host.docker.internal:11434`. Проверьте доступность daemon из контейнера.
Контейнерный путь в текущей сессии не проверен; основной проверенный путь —
приложение на хосте и native PostgreSQL с embedded Qdrant.

## Troubleshooting

### После обновления на `/` по-прежнему Swagger, 404 или инструкция сборки

Сначала соберите текущий frontend и синхронизируйте Python зависимости:

```sh
npm --prefix frontend ci
npm --prefix frontend run build
uv sync --locked
```

Остановите прежний API через `Ctrl+C` в его терминале и снова выполните ту же
команду `uv run --no-sync uvicorn` из раздела
[«Миграция и старт API»](#миграция-и-старт-api). Запущенный процесс без `--reload`
сам не подхватывает изменения Python. После перезапуска откройте
[http://127.0.0.1:8000/](http://127.0.0.1:8000/) и обновите страницу.
Новая версия отдаёт GUI; `/docs` по-прежнему открывает Swagger.
Не запускайте второй экземпляр API с той же директорией embedded Qdrant.

### Embedded Qdrant: `Storage folder ... is already accessed`

Работающий экземпляр `QdrantClient` удерживает блокировку `.data/qdrant/.lock`.
Embedded Qdrant допускает только один процесс приложения: используйте `--workers 1`
и перед повторным запуском остановите предыдущий API. Из корня репозитория проверьте
владельца блокировки и процесс, слушающий порт 8000:

```sh
lsof -nP .data/qdrant/.lock
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Остановите свой предыдущий сервер через `Ctrl+C` в его терминале, затем повторите
команду запуска API. Само наличие файла `.lock` нормально; блокировка освобождается
при закрытии клиента или завершении процесса. Не удаляйте `.lock` или `.data`:
удаление файла не останавливает владельца блокировки, а `.data` содержит индекс
и другие локальные данные. Для нескольких процессов используйте Qdrant server
из варианта Compose.

### Hugging Face: предупреждение об unauthenticated requests

В стандартном Ollama backend загружается публичный tokenizer `Qwen/Qwen3.5-4B`.
Предупреждение об отсутствии `HF_TOKEN` само по себе не мешает этому запуску;
токен не обязателен. Авторизация нужна для opt-in HF backend с gated-моделью
`google/embeddinggemma-300m`, как описано в README.

## Первый вопрос в браузере

1. Откройте [приложение](http://127.0.0.1:8000/). На вкладке «Создать аккаунт»
   введите email и пароль 12–128 символов; если аккаунт уже есть, выберите «Войти».
2. Добавьте материал через боковую панель. Для примера используйте
   [gradient-descent-ru.md](../examples/materials/gradient-descent-ru.md).
   Статус меняется «В очереди» → «Индексируется» → «Готов»; вопрос задавайте после готовности.
3. Нажмите «Новый чат», задайте название и подтвердите «Создать».
   Спросите `Что такое градиентный спуск и как меняются параметры?`.
4. Под ответом нажмите «Открыть фрагмент» в карточке источника.
   В том же чате спросите `Покажи численный пример из материала.`;
   в демонстрационном материале первый шаг равен `4 - 0.1 * 8 = 3.2`.
5. Обновите страницу или выберите предыдущий чат: сообщения и источники
   восстанавливаются из PostgreSQL.

Для английского примера загрузите
[probability-en.md](../examples/materials/probability-en.md), дождитесь «Готов»
и в новом чате спросите
`What are the expectation and variance of a Bernoulli random variable?`.

Enter отправляет, Shift+Enter добавляет строку. Максимум — 4000 символов.
Состояние ожидания отражает работу локальной модели; SSE передаёт готовый ответ,
а не отдельные токены генерации. Источники перечитываются через REST после run.
Кнопка «Повторить запрос» сохраняет исходные UUID и вопрос; разрыв соединения
сам по себе не означает, что сервер не сохранил ответ.

Материал со статусом «Ошибка» показывает причину и действие «Повторить»;
удаление требует подтверждения. Если удалённый источник уже нельзя открыть,
его excerpt остаётся в сохранённом сообщении. Выход или ответ 401 после истечения JWT
возвращают к форме входа; чужие материалы, чаты и источники недоступны.

## Разработка интерфейса

Запустите backend на 8000, затем в другом терминале:

```sh
npm --prefix frontend run dev
```

Vite работает на `http://127.0.0.1:5173/` и проксирует `/rest` и `/rpc` в FastAPI.
Для production использования соберите frontend и открывайте порт 8000.
Отдельный Vite server в этом режиме не нужен.

## Первый вопрос через Swagger

Раскройте нужную операцию, нажмите **Try it out**, заполните поля и нажмите
**Execute**. Результат запроса отображается в **Response body**.

1. В `POST /rest/study-rag/users/` задайте собственные `email_address` и
   `password_text` длиной 12–128 символов. Ответ 201 содержит `access_token`.
   Повторный вход — `POST /rest/study-rag/sessions/` с теми же полями.
2. Нажмите **Authorize** и вставьте значение `access_token`. Swagger сам добавляет
   `Authorization: Bearer ...`. `GET /rest/study-rag/users/current/` проверяет вход.
3. В `POST /rest/study-rag/documents/` выберите файл в multipart поле `file`.
   Для воспроизводимого примера подходит
   [gradient-descent-ru.md](../examples/materials/gradient-descent-ru.md).
   Сохраните `document_identifier` из ответа 202.
4. Вызывайте `GET /rest/study-rag/documents/{document_identifier}/` до состояния
   `ready` и ненулевого `chunk_count`. Ответ upload означает сохранённую задачу;
   обычный переход — `queued → indexing → ready`.
5. Создайте чат через `POST /rest/study-rag/chats/`, например с
   `{"title_text":"Градиентный спуск"}`. Сохраните `chat_identifier`.
6. Отправьте `POST /rest/study-rag/messages/` с JSON ниже. Замените обе строки UUID:
   первая — чат из предыдущего шага, вторая — новый UUID запроса.

```json
{
  "chat_identifier": "<UUID созданного чата>",
  "client_request_identifier": "<новый UUID запроса>",
  "question_text": "Что такое градиентный спуск и как меняются параметры?"
}
```

Новый UUID можно получить без обращения к сервису:

```sh
uv run --no-sync python -c 'import uuid; print(uuid.uuid4())'
```

Текст ответа находится в `assistant_message.content_text`. Метки `[S1]` и другие
сопоставляются с `assistant_message.citation_records`: там находятся UUID
документа/фрагмента, excerpt, имя файла, score, `source_url` и страницы PDF.
Для TXT/Markdown страницы равны `null`. Открывайте citation через
`GET /rest/study-rag/chunks/{chunk_identifier}/` в Swagger с тем же токеном.

Для уточнения отправьте `Покажи численный пример из материала.` в тот же чат
с новым `client_request_identifier`. В демонстрационном материале первый шаг
равен `4 - 0.1 * 8 = 3.2`. История доступна через
`GET /rest/study-rag/messages/` с query-параметром `chat_identifier`; список чатов —
через `GET /rest/study-rag/chats/`. После перезапуска используйте login и прежний UUID чата.

Английский пример: загрузите [probability-en.md](../examples/materials/probability-en.md),
дождитесь `ready`, создайте новый чат и спросите
`What are the expectation and variance of a Bernoulli random variable?`.

Повтор завершённого запроса с тем же UUID и текстом возвращает сохранённый ответ.
Другой вопрос под тем же UUID или параллельный запрос активного чата получает 409.
При ошибке зависимости turn становится `failed`; тот же UUID и текст допускают
повтор. При недостатке найденного контекста ответ содержит
`sufficient_context=false` и пустые citations; такой отказ сохраняется в истории.

## Ошибка, retry и удаление документа

При `failed` смотрите `error_code` и `error_message` document GET. После устранения
причины вызовите `POST /rest/study-rag/indexing-jobs/`:

```json
{
  "document_identifier": "<UUID failed документа>"
}
```

Затем снова ждите `ready`. Retry повторной queued/indexing задачи возвращает её
текущее состояние; retry ready/deleting документа получает 409. PDF без текстового
слоя получает `no_extractable_text`; для него нужен другой исходный файл, поскольку
OCR в этот этап не входит.

`DELETE /rest/study-rag/documents/{document_identifier}/` возвращает 202 и
`deleting`: документ сразу исключается из поиска. Worker очищает vectors, файл и
chunks, затем document GET возвращает 404. При временной ошибке очистки `deleting`
сохраняется с кодом ошибки, worker повторяет очистку с задержкой.
История и citation snapshots остаются; URL удалённого chunk возвращает 404.
Чужие document/chat/chunk UUID также возвращают 404.

## Смена embedding profile

Profile вычисляется из backend, модели, tokenizer, размерности, prefixes и chunk
настроек. Его не задают вручную. Чтобы перейти на оригинальные HF embeddings:

1. Примите условия [google/embeddinggemma-300m](https://huggingface.co/google/embeddinggemma-300m)
   и авторизуйте Hugging Face локально.
2. Установите optional зависимости и выполните локальный вход:

```sh
uv sync --locked --group dev --extra huggingface
uv run --no-sync hf auth login
```

3. В `.env` установите `STUDY_RAG_EMBEDDING_BACKEND=huggingface`. При необходимости
   задайте `STUDY_RAG_EMBEDDING_DEVICE=mps` или `cpu`, затем перезапустите API.
4. Повторно загрузите исходные файлы и дождитесь `ready` для новых документов.

Deduplication работает по `(tenant, SHA-256, profile)` среди не удалённых документов.
Одинаковый файл в том же profile возвращает существующий UUID. После смены profile
upload создаёт новый документ с новым UUID. Поиск использует только ready документы
текущего profile; старые документы доступны владельцу и допускают обычное удаление.
Retry старого indexing job не переводит его в новый profile: возможен
`embedding_profile_changed`, после которого требуется повторный upload.

Original HF adapter использует float32 и собственный tokenizer Gemma. Реальная
загрузка и инференс этих gated весов в текущей сессии не подтверждены; default
Ollama inference и публичный Qwen tokenizer проверены отдельно.

## Автоматическая проверка и остановка

Frontend tests и production build:

```sh
npm --prefix frontend test
npm --prefix frontend run build
```

Python checks, PostgreSQL test DB и границы обычного pytest описаны в
[ci.md](ci.md). Транспортные тесты не заменяют проверку браузерного сценария
с настоящими локальными моделями.

Для запущенного сервиса с настоящими локальными моделями:

```sh
uv run --no-sync python scripts/smoke_local.py --base-url http://127.0.0.1:8000
```

Smoke загружает собственные RU/EN fixtures, проверяет retrieval, citations,
численный follow-up, историю, replay, два tenant, отказ без ответа и удаление.
Он очищает загруженные документы; созданные аккаунты и история остаются, поскольку
API удаления пользователя в MVP отсутствует. Это проверка поведения на маленьких
примерах. Пользовательского корпуса пока нет; accuracy/recall на книгах не измерены.
[evaluation.jsonl](../examples/evaluation.jsonl) — образец будущего корпуса оценки.

API останавливается `Ctrl+C`. Для native PostgreSQL:

```sh
STUDY_RAG_PG_BIN="$(brew --prefix postgresql@17)/bin"
"$STUDY_RAG_PG_BIN/pg_ctl" -D "$PWD/.local/postgres" -m fast stop
```

Для Compose:

```sh
docker compose down
```

Volumes Compose, `.local/postgres` и `.data` сохраняют данные между запусками.
Очередь находится в PostgreSQL: queued задачи и indexing задачи с истёкшим lease
возобновляются после старта. `.env`, `.data`, `.local` и распакованный локальный
архив правил в `.agents` исключены из репозитория.

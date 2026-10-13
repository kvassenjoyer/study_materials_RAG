# Установка, запуск и обслуживание

Команды выполняются из корня репозитория. Основной пользовательский путь - [README](../README.md) и [руководство](user-guide.md); полный список переменных - [configuration.md](configuration.md)

## Подготовка

Нужны Python 3.12/3.13, uv, Node.js >=22.12 с npm, PostgreSQL и Ollama. CI/Docker используют Python 3.12 и Node.js 26. Установка из lock-файлов:

```powershell
uv sync --locked --group dev
npm --prefix frontend ci
npm --prefix frontend run build
ollama pull embeddinggemma:300m
ollama pull qwen3.5:4b
ollama list
```

Запустите Ollama приложением или `ollama serve` в отдельном терминале. Для будущего offline запуска сначала скачайте tokenizer при обычном startup. Приложение не устанавливает PostgreSQL и модели Ollama автоматически

## Настройка окружения

Следующая команда для PowerShell создаёт `.env` из `.env.example` со случайным JWT-секретом. Существующий `.env` остаётся без изменений. Выполняйте её после установки зависимостей:

```powershell
@'
import pathlib
import secrets

configuration = pathlib.Path(".env.example").read_text(encoding="utf-8")
configuration = configuration.replace(
    "STUDY_RAG_JWT_SECRET=\n",
    "STUDY_RAG_JWT_SECRET=" + secrets.token_hex(32) + "\n",
    1,
)
try:
    with pathlib.Path(".env").open("x", encoding="utf-8") as destination:
        destination.write(configuration)
except FileExistsError:
    print(".env уже существует и не изменён")
'@ | uv run --no-sync python -
```

`STUDY_RAG_DATABASE_URL` обязательно использует `postgresql+asyncpg`. Секрет должен содержать минимум 32 UTF-8 байта. Настройки окружения процесса приоритетнее `.env`.

## PostgreSQL и Qdrant через Compose

Можно поднять только PostgreSQL, оставив Qdrant embedded:

```powershell
docker compose up -d database
docker compose ps
```

.env.example содержит подходящий адрес: postgresql+asyncpg://study_rag:study_rag@127.0.0.1:5432/study_rag. Это локальные credentials из compose.yaml

Для отдельного Qdrant server:

```powershell
docker compose up -d database qdrant
```

Добавьте в .env STUDY_RAG_QDRANT_URL=http://127.0.0.1:6333. Без этой переменной используется .data/qdrant. Пустая строка не заменяет отсутствие настройки

Порты Compose опубликованы на localhost. Database health должен стать healthy. Существующие данные embedded Qdrant автоматически в server не копируются: смена адреса на пустой индекс требует переиндексации документов активного profile

## Native Windows

Если установлен PostgreSQL со своим сервисом, используйте его рабочий host/port/роль/БД. Порт не определяется операционной системой: 5432 в примере и 55432 у отдельного macOS cluster - разные настройки

Для новой локальной установки PostgreSQL, где доступны psql/createdb, можно один раз создать роль и БД:

```powershell
psql -h 127.0.0.1 -p 5432 -U postgres -c "CREATE ROLE study_rag LOGIN PASSWORD 'study_rag';"
createdb -h 127.0.0.1 -p 5432 -U postgres -O study_rag study_rag
```

Команды запросят пароль администратора PostgreSQL; не повторяйте CREATE для уже настроенной роли/БД. Если bin не в PATH, запускайте psql/createdb из каталога установленного PostgreSQL. После создания используйте адрес из .env.example

Embedded Qdrant не требует отдельной Windows-службы. Для удаления URL из текущего PowerShell:

```powershell
Remove-Item Env:STUDY_RAG_QDRANT_URL -ErrorAction SilentlyContinue
```

Переменную также уберите из .env. Shell variables не очищают значение в файле

## Native macOS

Следующий вариант создаёт отдельный localhost cluster PostgreSQL 17 в .local/postgres. initdb/createdb выполняются один раз. Понадобится установленный Homebrew PostgreSQL 17:

```sh
brew install postgresql@17
STUDY_RAG_PG_BIN="$(brew --prefix postgresql@17)/bin"
mkdir -p .local/postgres-socket
"$STUDY_RAG_PG_BIN/initdb" -D "$PWD/.local/postgres" --username=study_rag --encoding=UTF8 --locale=C --auth-local=trust --auth-host=trust
"$STUDY_RAG_PG_BIN/pg_ctl" -D "$PWD/.local/postgres" -l "$PWD/.local/postgres.log" -o "-h 127.0.0.1 -p 55432 -k \"$PWD/.local/postgres-socket\"" start
"$STUDY_RAG_PG_BIN/createdb" -h 127.0.0.1 -p 55432 -U study_rag study_rag
```

Trust относится только к этой конфигурации персонального localhost cluster. В .env задайте STUDY_RAG_DATABASE_URL=postgresql+asyncpg://study_rag@127.0.0.1:55432/study_rag, Qdrant URL оставьте незаданным

Повторный запуск использует pg_ctl start без initdb/createdb. Windows инструкция не предполагает наличие Homebrew, Unix sockets или .venv/bin

## Миграции, запуск и обновление

```powershell
uv run --no-sync alembic upgrade head
uv run --no-sync uvicorn study_rag.main:create_application --factory --host 127.0.0.1 --port 8000 --workers 1
```

Startup требует revision f1c7e56b348d и все ожидаемые таблицы/колонки, сам миграции не применяет. При embedded Qdrant нужен один процесс, второй сервер с теми же данными запускать нельзя

[GUI](http://127.0.0.1:8000/), [Swagger](http://127.0.0.1:8000/docs), [OpenAPI](http://127.0.0.1:8000/openapi.json). Health:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/rest/study-rag/health/
```

Успех - status_name=ready. Проверяются PostgreSQL/schema, Qdrant, модели и живые worker tasks. Health не подтверждает качество retrieval или полноценный inference

Обновление: Ctrl+C в терминале сервера, uv sync --locked --group dev, npm ci/build при изменении frontend/dependencies, alembic upgrade head, повтор запуска. Python/.env без reload не подхватываются. Frontend build читается с диска, после него обновите браузер Ctrl+F5

Если Windows запрещает остановить процесс из другого shell, используйте Ctrl+C в том терминале, где сервер запущен. Не удаляйте Qdrant lock и не завершайте все Python-процессы компьютера

Для разработки frontend:

```powershell
npm --prefix frontend run dev
```

Vite на 5173 проксирует backend на 8000. Production не требует отдельного Vite server

## Приложение в контейнере

```powershell
docker compose up -d database qdrant
docker compose --profile application build application
docker compose --profile application run --rm application uv run --no-sync alembic upgrade head
docker compose --profile application up -d application
```

Dockerfile включает Node build и Python runtime. Compose переопределяет DATABASE_URL на database:5432, QDRANT_URL на qdrant:6333, OLLAMA_URL на host.docker.internal:11434; uploads/cache лежат в application-data volume

Ollama остаётся на хосте и должна быть доступна из контейнера. host.docker.internal само по себе не меняет binding Ollama. Миграционный run следует выполнять без параллельно работающего старого application при обновлении схемы

Контейнерный deployment в текущей актуализации документации не выполнялся. Существующий рабочий запуск на хосте не является доказательством работоспособности контейнера

## API из PowerShell

Для обычных действий удобен Swagger. Следующий полностью исполняемый пример для **PowerShell 7** входит в существующий аккаунт, выбирает первый проект, загружает выбранный файл и запускает вопрос. Создание аккаунта при необходимости выполните через GUI

```powershell
$ragBaseUrl = "http://127.0.0.1:8000/rest/study-rag"
$ragLogin = Read-Host "Логин"
$ragSecurePassword = Read-Host "Пароль" -AsSecureString
$ragPassword = [System.Net.NetworkCredential]::new("", $ragSecurePassword).Password
$ragCredentials = @{login_name=$ragLogin; password_text=$ragPassword} | ConvertTo-Json
$ragAuth = Invoke-RestMethod -Method Post -Uri "$ragBaseUrl/sessions/" -ContentType "application/json" -Body $ragCredentials
$ragHeaders = @{Authorization="Bearer $($ragAuth.access_token)"}
$ragCredentials = $null
$ragPassword = $null
$ragProjects = Invoke-RestMethod -Uri "$ragBaseUrl/projects/" -Headers $ragHeaders
if ($ragProjects.items.Count -eq 0) { throw "Сначала создайте проект в интерфейсе" }
$ragProjectId = $ragProjects.items[0].project_identifier
$ragFilePath = Read-Host "Полный путь к материалу"
$ragFile = Get-Item -LiteralPath $ragFilePath
$ragDocument = Invoke-RestMethod -Method Post -Uri "$ragBaseUrl/documents/?project_identifier=$ragProjectId" -Headers $ragHeaders -Form @{file=$ragFile}
$ragDeadline = [DateTime]::UtcNow.AddMinutes(30)
do {
    Start-Sleep -Seconds 2
    $ragDocument = Invoke-RestMethod -Uri "$ragBaseUrl/documents/$($ragDocument.document_identifier)/" -Headers $ragHeaders
    Write-Host $ragDocument.state_name $ragDocument.indexing_progress_percent
    if ([DateTime]::UtcNow -ge $ragDeadline) { throw "Индексация ещё не завершена" }
} while ($ragDocument.state_name -in @("queued", "indexing"))
if ($ragDocument.state_name -ne "ready") { throw $ragDocument.error_code }
$ragChatBody = @{project_identifier=$ragProjectId} | ConvertTo-Json
$ragChat = Invoke-RestMethod -Method Post -Uri "$ragBaseUrl/chats/" -Headers $ragHeaders -ContentType "application/json" -Body $ragChatBody
$ragQuestion = Read-Host "Вопрос"
$ragRequestId = [guid]::NewGuid().ToString()
$ragQuestionBody = @{chat_identifier=$ragChat.chat_identifier; client_request_identifier=$ragRequestId; question_text=$ragQuestion} | ConvertTo-Json
$ragJob = Invoke-RestMethod -Method Post -Uri "$ragBaseUrl/answer-jobs/" -Headers $ragHeaders -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($ragQuestionBody))
$ragJob
```

После 202 наблюдайте active jobs, затем историю чата. GET active jobs не возвращает completed/failed:

```powershell
Invoke-RestMethod -Uri "$ragBaseUrl/answer-jobs/" -Headers $ragHeaders
Invoke-RestMethod -Uri "$ragBaseUrl/messages/?chat_identifier=$($ragChat.chat_identifier)&limit=100" -Headers $ragHeaders
```

Остановить созданную выше задачу:

```powershell
Invoke-RestMethod -Method Post -Uri "$ragBaseUrl/answer-jobs/$($ragJob.turn_identifier)/stop/" -Headers $ragHeaders
```

Принудительная переиндексация выбранного выше ready документа после изменения parser:

```powershell
$ragRebuildBody = @{document_identifier=$ragDocument.document_identifier; rebuild=$true} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$ragBaseUrl/indexing-jobs/" -Headers $ragHeaders -ContentType "application/json" -Body $ragRebuildBody
```

Для обычного retry failed передайте только document_identifier. Original PDF доступен по /documents/{document_identifier}/original/ с тем же Bearer; ссылки без заголовка авторизации в адресной строке не заменяют GUI viewer

## Смена профиля и восстановление данных

Смена embedding backend/model/tokenizer/chunk settings требует новой загрузки исходных материалов. Rebuild сохраняет прежний profile и не является конвертером индекса. Изменение parser при неизменном profile применяется явным rebuild

PostgreSQL, оригиналы и Qdrant нужно сохранять согласованно. Встроенной кнопки backup/restore нет. Ручная схема: остановить API, сделать PostgreSQL backup стандартными pg_dump/pg_restore и сохранить uploads вместе с Qdrant directory или server snapshot. Отдельно сохраните .env и model configuration; HF cache можно скачать заново

Не копируйте embedded Qdrant во время работы клиента. Удаление .data не исправляет блокировку и уничтожает пользовательские файлы/индекс. Восстановленный пустой vector store сам не переиндексирует ready строки PostgreSQL: запустите rebuild для активного profile либо восстановите соответствующий Qdrant backup

Смена JWT secret инвалидирует ранее выданные токены. .env, .data, .local и generated web assets игнорируются Git; wheel/контейнер должны собираться после frontend build

## Устранение проблем

| Симптом | Проверка и действие |
| --- | --- |
| На / инструкция сборки | npm --prefix frontend run build, затем refresh; API независимо доступен |
| Storage folder already accessed | Один Uvicorn worker; остановить прежний процесс через его терминал, lock не удалять |
| database_schema_unavailable | Проверить адрес нужной БД, выполнить alembic upgrade head до запуска |
| Нет модели / Ollama недоступна | ollama list, доступность 11434, точные теги из .env |
| HF warning без токена | Для публичного Qwen tokenizer токен обычно не нужен; warning не равен отказу |
| HF offline и cache отсутствует | Сначала скачать нужные артефакты с доступом к HF |
| Ошибка DNS при установке | Проверить доступ к npm/PyPI/HF/Ollama registry, VPN/proxy; текущий код не обходит сеть автоматически |
| Процент долго 0 | Parser/tokenizer ещё не определил chunks; это не доля прочитанных страниц |
| Обзор долго ожидается | Проверить queued/pending; несколько этапов extraction/review/synonyms, бюджет обработки 240 с плюс очередь |
| model_response_truncated | Length повтор уже выполнен; сократить/разделить вопрос либо согласованно изменить output/context budget |
| model_request_timeout / answer_timeout | Причина различает вызов модели и весь turn; проверить Ollama и размер вопроса |
| embedding_input_too_long | Даже вопрос до 4000 символов может превысить formatted embedding budget |
| Старый неправильный ответ повторяется | Completed replay возвращает сохранённый ответ; новый вопрос требует нового request UUID |
| Старый PDF-текст не изменился | Обновление parser не меняет ready chunks; выполнить явный rebuild |
| Пустой проект после удаления всех | Создать новый, удалённый default не восстанавливается |
| Вход снова требуется | JWT истёк/secret изменён; refresh tokens нет, войти заново |

Логи adaptive_search содержат scope, поисковые формулировки и counts. Полные документы приложением намеренно не логируются, но вопросы/поисковые формулировки могут содержать пользовательский текст. Не публикуйте логи целиком без просмотра

## Проверки и остановка

Команды и результаты: [CI](ci.md), [backend](backend-validation.md), [GUI](gui-validation.md). Real smoke работает с собственными fixtures и реальными моделями; обычные tests используют doubles

API останавливается Ctrl+C. Queued ответы остаются, выполняемый ответ при штатной отмене становится failed/answer_cancelled; после аварии pending станет failed по lease. Индексация повторяется по истечении своего lease. Подробности: [очередь](answer-queue.md)

Для созданного выше macOS cluster:

```sh
STUDY_RAG_PG_BIN="$(brew --prefix postgresql@17)/bin"
"$STUDY_RAG_PG_BIN/pg_ctl" -D "$PWD/.local/postgres" -m fast stop
```

Для Compose: `docker compose down` без удаления volumes. Native Windows PostgreSQL service обычно остаётся независимым от остановки API

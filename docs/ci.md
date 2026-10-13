# Проверки и CI

Источник истины: [checks.yml](../.github/workflows/checks.yml), [pyproject.toml](../pyproject.toml), [frontend/package.json](../frontend/package.json). Сверено 13 октября 2026 года

## GitHub Actions

Workflow запускается на push, pull_request и workflow_dispatch. Один job работает на Ubuntu 24.04, Python 3.12, Node.js 26, uv 0.12.23 и PostgreSQL 17. Actions закреплены по SHA, contents permission read-only, concurrency отменяет устаревший run той же группы, job timeout 20 минут

Python зависимости ставятся uv sync --locked --group dev, frontend - npm ci по lock-файлу. Optional huggingface extra не ставится. Затем выполняются:

```sh
npm --prefix frontend test
npm --prefix frontend run build
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync mypy --strict .
uv run --no-sync flake8 src tests scripts
uv run --no-sync pytest -q --strict-markers -m "not local_models"
```

Build выполняет tsc --noEmit и Vite, создаёт web assets до Python tests статического hosting. CI не запускает браузерный Playwright smoke и не проверяет контейнерный deployment или настоящую Ollama

## Изоляция тестов

TEST_DATABASE_URL указывает на PostgreSQL service job. Repository tests создают уникальные схемы rag_test_UUID, применяют Alembic на injected connection и удаляют схему в finally. Рабочая .env и предварительная миграция public schema в CI не требуются

Qdrant tests используют embedded-клиент в tmp_path; отдельный Qdrant server в CI не поднимается. Каждый тест получает собственные ресурсы, pytest запускается последовательно без xdist

Model tests внедряют tokenizer/encoder doubles и fake HTTP Ollama. HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1 и HF_HUB_DISABLE_TELEMETRY=1 ограничивают внешние обращения соответствующих библиотек. Marker local_models исключён из workflow; сам по себе marker не запускает настоящую модель

AG-UI tests используют настоящий adapter и подменённую сценарную/queue границу, проверяют auth, body/protocol limits, lifecycle, ошибки и разрыв ожидания. Queue/repository tests отдельно проверяют FIFO, stop, deletion, lease, replay и владение

## Повторение локально

Установите зависимости и соберите frontend по README. Для полного прогона нужна отдельная тестовая БД с правом создавать/удалять схемы. Пример для Compose:

```powershell
docker compose up -d database
docker compose exec database createdb -U study_rag study_rag_test
$env:TEST_DATABASE_URL = "postgresql+asyncpg://study_rag:study_rag@127.0.0.1:5432/study_rag_test"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
uv run --no-sync pytest -q --strict-markers -m "not local_models"
```

createdb выполняется один раз; ошибка «уже существует» означает, что БД создана ранее. Для native PostgreSQL задайте адрес именно своей тестовой БД. Не копируйте private .env в Git и не печатайте credentials

Bash использует export TEST_DATABASE_URL вместо PowerShell $env:. Без TEST_DATABASE_URL PostgreSQL-проверки будут skipped, что не подтверждает работу repository integration

Точные наблюдавшиеся результаты и ограничения: [backend-validation.md](backend-validation.md), [gui-validation.md](gui-validation.md)

## Реальный smoke

```powershell
uv run --no-sync python scripts/smoke_local.py --base-url http://127.0.0.1:8000
```

Нужен запущенный сервер с моделями, доступными через Ollama. Скрипт загружает собственные RU/EN fixtures, проверяет supported answers/citations, численный follow-up, replay, history, 404 чужих объектов и общие ответы без sources

Запросы идут через синхронный /messages/, который ждёт серверную очередь. Скрипт не проверяет обзор всей книги, интерфейс, themes, original PDF endpoint или все операции проектов. В finally он запрашивает удаление созданных документов, не ожидая полного завершения cleanup; аккаунты и история остаются

## Поддержание документации

После изменения schemas/routes обновляйте [contracts.md](contracts.md), Settings - [configuration.md](configuration.md), поведения пользователя - [user-guide.md](user-guide.md). Не переносите числа tests старого этапа в описание текущего результата

Успешный unit/contract test подтверждает проверяемый сценарий, а не качество генерации. Реальную LLM и browser smoke отмечайте отдельно. Для новых env-параметров проверяйте default, bounds, composition root и влияние на embedding profile

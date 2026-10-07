# Проверки в GitHub Actions

[Workflow](../.github/workflows/checks.yml) запускается для push, pull request и вручную
через workflow dispatch. Один job на Ubuntu 24.04 использует Python 3.12, uv 0.12.23,
Node.js 26 и PostgreSQL 17. Action revisions закреплены по SHA.

Зависимости устанавливаются командой `uv sync --locked --group dev`: несогласованные
`pyproject.toml` и `uv.lock` прерывают проверку. Optional extra `huggingface` не устанавливается;
тесты HF adapter получают encoder через DI. После установки команды используют `--no-sync`.

Frontend устанавливается через `npm ci` из `frontend/package-lock.json`.
Vitest проверяет транспортные и UI boundary контракты, а build запускает
TypeScript check (`tsc --noEmit`) и Vite. Собранный GUI лежит в
`src/study_rag/web`; frontend сборка предшествует Python tests, чтобы проверить
static hosting готовых assets. npm cache зависит от frontend lock-файла.

Проверки выполняются последовательно после установки зависимостей:

```bash
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync mypy --strict .
uv run --no-sync flake8 src tests scripts
uv run --no-sync pytest -q --strict-markers -m "not local_models"
```

`TEST_DATABASE_URL` указывает на временный PostgreSQL service этого job. Каждый
repository test создаёт собственную схему, применяет Alembic migrations на injected
connection и удаляет схему при завершении. Предварительный `alembic upgrade head`
и рабочая `.env` для CI не нужны. Qdrant tests сохраняют embedded index в `tmp_path`;
отдельный Qdrant service не требуется. До настройки параллельной изоляции pytest
запускается без xdist.

Обычные тесты используют подготовленные tokenizer/encoder и fake HTTP для Ollama.
Переменные `HF_HUB_OFFLINE=1` и `TRANSFORMERS_OFFLINE=1` блокируют загрузку моделей
через эти библиотеки. Tests с marker `local_models` исключены из job; реальные веса
и Ollama daemon в CI не нужны.

AG-UI tests используют настоящий adapter и fake ConversationService на границе;
они проверяют SSE lifecycle, входные ограничения, auth, сохранение run UUID,
отмену запроса и безопасные ошибки. Replay проверяется тестами сценария переписки.
Успешный build подтверждает типы и сборку frontend, но не заменяет браузерную
проверку взаимодействия с настоящими локальными моделями.

Настоящая проверка EmbeddingGemma и Qwen остаётся ручным
`uv run python scripts/smoke_local.py` против запущенного локального сервиса, как
описано в README. Успешный CI подтверждает контракты, типы и интеграции с PostgreSQL
и embedded Qdrant, frontend контракты и production сборку;
качество реальной генерации оценивается отдельно.

Для повторения проверок на своей машине используйте Node.js 26,
`npm --prefix frontend ci` и `uv sync --locked --group dev`
и задайте `TEST_DATABASE_URL` отдельной тестовой БД с правом создавать схемы.
Без этой переменной PostgreSQL tests локально будут skipped. Credentials внутри
workflow относятся только к эфемерному service и не требуют repository secrets.

Использованные контракты Actions и uv:
[uv в GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/),
[setup-uv](https://github.com/astral-sh/setup-uv),
[setup-node](https://github.com/actions/setup-node),
[PostgreSQL service](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers).

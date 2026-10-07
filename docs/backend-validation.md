# Проверка backend Mini-RAG

Дата проверки: 4 октября 2026 года. Требования сохранены в
[requirements.md](requirements.md), сценарий запуска — в [runbook.md](runbook.md),
детали инференса — в [local-models.md](local-models.md), автоматические проверки
репозитория — в [ci.md](ci.md).

Проверки ниже разделяют настоящее хранилище/инференс и тестовые адаптеры.
Количество прошедших тестов в отдельных запусках не складывается в итоговую цифру.

## Уже подтверждённые проверки

| Проверка | Команда | Результат |
| --- | --- | --- |
| PostgreSQL repositories и миграции | `.venv/bin/pytest -q tests/integration/test_repositories.py` при заданном `TEST_DATABASE_URL` | 18 passed |
| JWT/Argon2 и embedded Qdrant | `.venv/bin/pytest -q tests/integration/test_security.py tests/integration/test_vector_store.py` | 19 passed |
| Хранение файлов, parser, tokenizer, embeddings, generation | Команда ниже | 48 passed |

```sh
.venv/bin/pytest -q tests/unit/test_file_storage.py tests/unit/test_parser.py \
  tests/unit/test_tokenizer.py tests/unit/test_embeddings.py tests/unit/test_generation.py
```

PostgreSQL проверялся на настоящем PostgreSQL 17. Каждая integration fixture
создаёт отдельную случайную схему, выполняет Alembic migrations и удаляет схему
после теста. Проверены миграции, tenant isolation, claim concurrency, stale lease,
visibility ready документов, retry/delete, turn replay/concurrency, сохранение
citation snapshot и deduplication по tenant+digest+profile. Пустая или устаревшая
схема считается недоступной; startup не запускает миграции автоматически.

Security/Qdrant suite проверяет настоящий Argon2/JWT и embedded Qdrant с временным
дисковым хранилищем. Проверяются обязательные tenant/profile/document filters,
изоляция поиска и удаления, пустой список ready документов, validation векторов
и несовместимая конфигурация коллекции. Запуск Qdrant server в Docker сюда не входит.

Model suite проверяет границы и ошибки через управляемые doubles/HTTP responses:
atomic файловые пути и ограничения, UTF-8/BOM, PDF page mapping, scan refusal,
Unicode/tail/overlap, formatted budgets, prefixes один раз, запрет усечения,
dimension/NaN/zero rejection, retry/timeouts, history/context и citations.
Добавлены три regression теста нормализации citations: неизвестные inline labels
отклоняются, а известные declared labels без inline markers добавляются к ответу
детерминированно. Нормализация добавляет только метки проверенных источников и
не изменяет фактические утверждения. Эти 48 тестов не являются запуском весов
через настоящий model runtime.

## Настоящие локальные модели

Проверенная машина: Apple M3 Pro, arm64, 36 GB unified memory, Metal.
Использован существующий Ollama daemon `0.32.14` с точными тегами:

| Назначение | Модель | Проверенный артефакт |
| --- | --- | --- |
| Embeddings | `embeddinggemma:300m` | BF16, ID `85462619ee72`, около 622 MB |
| Answer/rewrite | `qwen3.5:4b` | Q4_K_M, ID `2a654d98e6fb`, около 3.4 GB |
| Chunk/generation tokenizer | `Qwen/Qwen3.5-4B` | Публичный HF tokenizer скачан в `.data/models` |

Проверены реальные production adapters parser/tokenizer/embed/generate на
собственных RU/EN примерах: embeddings содержат 768 конечных координат, норма 1.0,
ответы используют проверенные citations. На примерах о хлоропластах similarity
релевантного контекста составила 0.6449 для RU и 0.6654 для EN.

Отдельная реальная цепочка rewrite → embed → retrieve → generate на
[gradient-descent-ru.md](../examples/materials/gradient-descent-ru.md) сохранила тему
короткого RU follow-up. Scores двух chunks — 0.5487 и 0.4805 при неизменённом пороге
0.35. Ответ содержал `w_next = 4 - 0.1 * 8 = 3.2`, `sufficient_context=true`
и валидную citation; значение 3.2 проверено assertion.

Original HF adapter `google/embeddinggemma-300m` реализован и проверен doubles
на уровне границы. Реальный инференс оригинальных HF весов не выполнялся:
репозиторий gated, доступ требует принятия условий и локальной авторизации,
heavy optional extra в этой проверке не устанавливался. Рабочий default использует
EmbeddingGemma через локальный Ollama и HF tokenizer Qwen. Их tokenizer/budgets
явно различаются и входят в embedding profile.

## Итоговый запуск текущего backend

Статические проверки и полный pytest выполнены после добавления regression tests
citations. PostgreSQL suite использовала отдельную тестовую БД `study_rag_test`
на PostgreSQL 17, `127.0.0.1:55432`; рабочая БД приложения не использовалась.
Контролируемый перезапуск API завершил graceful shutdown и новый startup;
`GET /rest/study-rag/health/` вернул 200 с `status_name=ready`.
После перезапуска подтверждены сохранение исходных объектов, истории и replay.
Отдельный HTTP smoke с настоящими локальными моделями также завершился успешно.

| Проверка | Команда | Итог |
| --- | --- | --- |
| Ruff lint | `.venv/bin/ruff check .` | PASS |
| Ruff format | `.venv/bin/ruff format --check .` | PASS, 66 файлов |
| Strict mypy | `.venv/bin/mypy .` | PASS, 56 файлов, включая tests/scripts |
| Архивные COP правила | `.venv/bin/flake8 .` | PASS |
| Полный pytest с настоящим PostgreSQL | Точная команда ниже | 97 passed за 2.69 s, 0 skipped, 0 failed |
| HTTP smoke с настоящими локальными моделями после перезапуска | `.venv/bin/python scripts/smoke_local.py` | PASS, exit code 0 |

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  TEST_DATABASE_URL=postgresql+asyncpg://study_rag@127.0.0.1:55432/study_rag_test \
  .venv/bin/pytest -q --strict-markers -m 'not local_models'
```

Offline flags запрещают загрузку HF артефактов при этом pytest запуске. Реальные
локальные модели проверяются отдельно через HTTP smoke; marker `local_models`
в указанную pytest команду не включён.

Реальный HTTP smoke создаёт собственные временные аккаунты, загружает RU/EN
fixtures, ждёт ready и проверяет численный follow-up, citations, историю, replay,
tenant isolation, отказ без знаний и исключение удалённых документов из поиска.
Он не подменяет Ollama. Загруженные документы очищаются; аккаунты и история
остаются, поскольку endpoint удаления аккаунта в MVP не предусмотрен.

Точный итог CLI:

```text
PASS: real RU/EN retrieval, citations, chat history, replay, two-tenant isolation, refusal and deletion
```

Assertions проверяют численный follow-up `3.2`/`3,2`, inline marker каждой citation,
четыре сообщения в истории после двух turns, 404 для чужих документов/чатов/chunks
и точный replay без новых сообщений. Проверены отказы для пустой базы другого
пользователя, вопроса без ответа и уже удалённых документов.

## Настоящие HTTP проверки TXT и PDF

API и реальные модели проверены на собственных небольших fixtures до и после
контролируемого перезапуска. Результаты:

| Сценарий | Наблюдаемый результат |
| --- | --- |
| Регистрация / login / current user | 201 / 200 / 200 |
| Upload UTF-8 TXT | 202, затем ready; 1 chunk, `page_count=null` |
| Upload созданного PDF с текстовым слоем | 202, затем ready; 1 chunk, `page_count=1` |
| Вопрос по PDF | Ответ содержит Harven и 42 из fixture, `sufficient_context=true` |
| PDF citation | Страница 1; source chunk GET вернул 200 |
| История PDF чата | 2 сообщения: user и assistant |
| Перезапуск API | Graceful shutdown/startup; health 200 ready |
| Авторизация после перезапуска | Login 200; исходный Bearer token остаётся действительным |
| Документы после перезапуска | 2 исходных ready TXT/PDF records точно совпадают с сохранёнными snapshots |
| Чат после перезапуска | Chat GET и list 200; 1 исходный чат |
| История после перезапуска | 2 сообщения точно совпадают с полными snapshots; citation и source GET 200 |
| Replay после перезапуска | Исходный request UUID возвращает тот же AnswerResponse JSON без новых сообщений |
| Очистка TXT/PDF документов | Оба DELETE 202, затем GET 404; document list пуст |

Это проверка конкретного PDF с текстовым слоем и TXT, а не пользовательских
учебников. Содержание fixture специально создано для этой проверки. Временные
TXT/PDF fixtures и файл auth-state проверочного runner удалены; созданные
аккаунты и чаты остаются в локальной БД.

## Воспроизведение тестов

Сначала установите окружение и выполните обычные проверки:

```sh
uv sync --locked --group dev --python 3.12
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy .
uv run --no-sync flake8 .
```

Для полного pytest создайте отдельную тестовую БД. Для native cluster из runbook:

```sh
STUDY_RAG_PG_BIN="$(brew --prefix postgresql@17)/bin"
"$STUDY_RAG_PG_BIN/createdb" -h 127.0.0.1 -p 55432 -U study_rag study_rag_test
export TEST_DATABASE_URL=postgresql+asyncpg://study_rag@127.0.0.1:55432/study_rag_test
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --no-sync pytest -q --strict-markers -m 'not local_models'
```

Для PostgreSQL из Compose:

```sh
docker compose exec database createdb -U study_rag study_rag_test
export TEST_DATABASE_URL=postgresql+asyncpg://study_rag:study_rag@127.0.0.1:5432/study_rag_test
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --no-sync pytest -q --strict-markers -m 'not local_models'
```

`createdb` выполняется один раз. Без `TEST_DATABASE_URL` PostgreSQL suite помечается
skipped; такой результат не подтверждает интеграцию с PostgreSQL. Тестовый пользователь
должен иметь право создавать и удалять собственные схемы. Запускайте тесты последовательно.
Для настоящего model/API smoke сначала поднимите backend по runbook.

## Границы результата

Docker Compose/Dockerfile подготовлены, контейнерный запуск не проверен в этой
сессии. Проверенный Qdrant integration mode — embedded qdrant-client.
Пользовательского корпуса книг/методичек нет. Демонстрационные RU/EN материалы и
[evaluation.jsonl](../examples/evaluation.jsonl) созданы в репозитории; accuracy,
recall и качество формул на изображениях не измерены.

OCR, фотографии, рукописные конспекты и фронтенд AG-UI/Pydantic AI остаются за
границами текущего backend. Тесты labels и citations проверяют происхождение
ссылок, но не доказывают фактическую корректность каждого утверждения модели.

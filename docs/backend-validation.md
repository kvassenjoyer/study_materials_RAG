# Проверка backend

Дата актуализации: **13 октября 2026 года**. Этот отчёт отделяет свежие автоматические проверки от проверок реальных моделей предыдущего этапа

## Текущий автоматический прогон

| Проверка | Результат |
| --- | --- |
| Ruff format --check . | PASS, 94 файла |
| Ruff check . | PASS |
| mypy --strict . | PASS, 75 source files |
| flake8 src tests scripts | PASS |
| pytest -q --strict-markers -m "not local_models" | **254 passed, 0 skipped, 0 failed**, 19.50 с |

Локальное окружение: Windows, Python 3.13.7, uv 0.11.14. Команды выполнялись установленными инструментами .venv; версии CI задаются отдельно workflow

TEST_DATABASE_URL был явно передан из текущих локальных настроек подключения. Repository fixtures создали/мигрировали/удалили собственные схемы rag_test_UUID в доступном PostgreSQL; это **не отдельная тестовая БД** и не изменение пользовательских таблиц public schema. Для обычного воспроизведения рекомендована отдельная тестовая БД, как в [CI](ci.md)

HF_HUB_OFFLINE=1 и TRANSFORMERS_OFFLINE=1 запрещали соответствующие загрузки. Ollama HTTP и encoder/tokenizer в обычных тестах подменяются на инфраструктурной границе. Этот результат не подтверждает качество реальной генерации и не является измерением recall на книгах

## Что покрывают тесты

| Область | Наблюдаемые инварианты |
| --- | --- |
| Авторизация | Нормализация/валидация логина, старые login-почты, password/JWT, безопасные ошибки |
| Проекты | Владение, списки, независимый dedup, миграция старых данных, удаление |
| Чаты | Автоназвание, ручное имя, history, replay, request mismatch, busy, удаление |
| Очередь | FIFO, один глобальный pending, разные проекты/tenant, stop/deletion, lease, разрыв ожидания |
| Индексация | Claim/CAS/heartbeat, прогресс, публикация ready, частичный сбой/retry, cleanup |
| Хранилище | Атомарный ключ, traversal/symlink escape, размеры и удаление |
| Parser/tokenizer | UTF-8/BOM, PDF layout/fallback, страницы/колонки/абзацы, хвост/overlap, budgets |
| Embeddings/Qdrant | Prefix единожды, no-truncation, finite/size/norm, tenant/profile/document filters |
| Генерация | Context/history budget, JSON/labels, general answer, length retry, safe failures |
| Поиск/обзор | Масштаб, разделы, расширение, semantic review, quotes, synonyms, ограниченный охват |
| REST/AG-UI | DTO/status/auth, body/protocol limits, SSE lifecycle, очередь, ошибки |
| PDF original | Байты/headers, владелец до file access, deleting/non-PDF, явный rebuild ready |

Исходники проверок: [tests](../tests). Репозиторные тесты применяют реальные migrations и PostgreSQL constraints; Qdrant integration использует embedded tmp_path

## Работающий PDF: проверка предыдущего этапа этого дня

После исправления parser и перезапуска backend в рабочей сессии переиндексирован выбранный пользователем двухстраничный PDF. Состояние ready, 4/4 chunks. Проблемный fragment содержал 191 строку с отдельными словами, после обработки - 20 строк с сохранёнными колонками

Проверены реальные HTTP original/chunk endpoints: PDF побайтно совпадает с исходником по SHA-256, X-PDF-Page-Count=2, без Bearer 401, все четыре chunk endpoint возвращают исправленный текст. В текущей документационной задаче индексация повторно не запускалась

Это свидетельство для конкретного текстового PDF; не тест всего пользовательского корпуса и не гарантия восстановления любой PDF-вёрстки. Порядок rebuild и ограничения: [pdf-sources.md](pdf-sources.md)

## Исторические model smoke и границы

На предыдущих этапах были выполнены реальные RU/EN вопросы через Ollama, численный follow-up из gradient-descent-ru.md, citations, history/replay, изоляция пользователей и удаление. Эти старые результаты не переносились в текущую задачу как новый полный model smoke

Текущий [smoke_local.py](../scripts/smoke_local.py) проверяет реальные supported и general answers, а не старое поведение обязательного отказа без источников. Во время актуализации документации он не запускался: новые аккаунты/чаты и LLM-нагрузка для этой задачи не требовались

Optional HF weights inference, Qdrant server и контейнерный запуск сейчас не проверялись. CI Linux workflow описан по его содержимому, удалённый GitHub Actions run не выполнялся. Native macOS команды сохранены как вариант конфигурации, но не исполнялись на Windows

Качество на книгах, полнота каталогов, фактическая корректность каждого ответа и OCR не подтверждаются этими тестами. Числа старых прогонов 2026-10-04 исключены из текущей сводки

## Воспроизведение

Команды установки и тестовой БД: [ci.md](ci.md). Запуск приложения/моделей: [runbook.md](runbook.md), [local-models.md](local-models.md). Frontend и browser evidence описаны отдельно в [gui-validation.md](gui-validation.md)

Локальные журналы этой актуализации находятся в игнорируемой .local; они не являются переносимым test runner репозитория. Авторитетные автоматические тесты находятся в tests, workflow - в .github/workflows/checks.yml

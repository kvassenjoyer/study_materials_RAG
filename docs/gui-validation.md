# Проверка GUI Mini-RAG

Дата: 4 октября 2026 года. Реализован браузерный интерфейс из
[архитектуры GUI](frontend-architecture.md): React + TypeScript + Vite,
официальный `@ag-ui/client==1.0.1` и Pydantic AI
`AGUIAdapter` из `pydantic-ai-slim[ag-ui]==2.54.0`.
Инференс остаётся локальным через существующий RAG-сценарий и Ollama.

## Автоматические проверки

| Проверка | Результат | Граница проверки |
| --- | --- | --- |
| Полный pytest | 126 passed, без skipped/failed | Отдельная PostgreSQL test DB; HF offline; модельная граница подготовлена тестами |
| Ruff ALL и форматирование | Passed; форматирование проверило 70 файлов | Python-код и правила проекта |
| Strict mypy | Passed, 59 файлов | Строгая проверка типов |
| flake8 COP | Passed | Правила из предоставленного архива с явными исключениями проекта |
| Frontend Vitest | 11 passed | REST/AG-UI transport, сохранение UUID, stale callbacks, безопасный Markdown |
| TypeScript + Vite build | Passed | Production assets в `src/study_rag/web` |
| Python wheel | Web assets включены | `index.html`, favicon, CSS и JS; `.env` и приватный cache не включены |

AG-UI tests используют настоящий adapter и заменяют `ConversationService` на
границе. Они проверяют lifecycle, ограничения тела и протокола, авторизацию,
отмену и безопасные ошибки. История, replay, tenant isolation, индексация и
удаление проверяются также сценарными и repository/API tests.
Обычный pytest сам по себе не подтверждает настоящий инференс.

Для повторения frontend checks:

```sh
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
```

Python-команды и настройка отдельной test DB описаны в [ci.md](ci.md).
Проверенный wheel создан в
`.local/gui-validation/dist/study_materials_rag-0.1.0-py3-none-any.whl`.

## Настоящая проверка в браузере

Production GUI проверен на отдельном сервере `127.0.0.1:8001` с отдельной БД
`study_rag_gui_validation` и изолированными локальными данными.
Работающий пользовательский API на порту 8000 не перезапускался.
После проверки тестовый API на 8001 корректно остановлен, браузерная вкладка
закрыта и viewport восстановлен. Дополнительный API или Vite server не оставлен.

Проверенные действия и наблюдаемые результаты:

1. `/`, JavaScript, CSS и favicon вернули 200. Вход в тестовый аккаунт открыл библиотеку.
2. Через GUI загружен
   [gradient-descent-ru.md](../examples/materials/gradient-descent-ru.md).
   Upload вернул 202; после индексации интерфейс показал «Готов».
3. Создан чат. Вопрос через официальный `HttpAgent → AGUIAdapter →
   FunctionModel → ConversationService` получил настоящий ответ Ollama с источником S1.
4. Уточняющий вопрос отправлен клавишей Enter. Ответ содержал численный шаг
   `4 − 0.1 × 8 = 3.2`, изменение значения функции `16 → 10.24` и источник S2.
5. Карточка источника открыла полный защищённый chunk в панели внутри интерфейса.
6. После обновления страницы сохранились два вопроса, два ответа и citations.
7. Выход и повторный вход восстановили ту же библиотеку и чат.
8. Проверены мобильное меню и переход к desktop layout; viewport возвращён
   в исходное состояние. Console warnings/errors в этом сценарии отсутствовали.

Локальные снимки результата сохранены в `.local/gui-preview.jpg` и
`.local/gui-source-preview.jpg`; `.local` исключён из Git.

## Границы результата и запуск

Этот браузерный smoke покрывает Markdown upload, вход, чаты, ответы, источники,
историю и мобильное меню. Через браузер отдельно не проверялись регистрация,
загрузка TXT/PDF, indexing retry, удаление и expired-session 401.
Для соответствующего backend поведения есть API/unit/integration evidence;
предыдущий настоящий smoke с TXT/PDF зафиксирован в
[backend-validation.md](backend-validation.md).

Dockerfile и CI обновлены для Node.js 26 и сборки GUI. Контейнерный запуск
здесь не проверялся. Gated HF weights не загружались и не проверялись настоящим
инференсом; проверен default Ollama backend. Пользовательского корпуса оценки
пока нет, accuracy/recall на книгах не измерены. OCR и OpenRouter остаются
будущими этапами.

Для рабочего приложения соберите frontend, синхронизируйте зависимости и
выполните миграции по [runbook.md](runbook.md). Если прежний API уже запущен
без `--reload`, остановите его через `Ctrl+C` в его терминале и повторите:

```sh
uv run uvicorn study_rag.main:create_application --factory --host 127.0.0.1 --port 8000 --workers 1
```

После этого [корневой адрес](http://127.0.0.1:8000/) открывает GUI,
[Swagger](http://127.0.0.1:8000/docs) остаётся доступен отдельно.
Embedded Qdrant требует один процесс приложения; новый экземпляр с той же
директорией данных запускайте после остановки прежнего.

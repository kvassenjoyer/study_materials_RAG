# Архитектура frontend

Актуальная реализация: React + TypeScript + Vite, [frontend/src](../frontend/src). Сервис - RAGumentation, помощник - RAGent; имена централизованы в [branding.ts](../frontend/src/branding.ts)

## Сборка и transport

Vite собирает GUI в `src/study_rag/web`. FastAPI отдаёт /, /assets и /favicon.svg; Swagger/OpenAPI остаются отдельно. В Git web output не хранится, Hatch включает собранные assets в wheel. Dockerfile собирает их отдельным Node stage

Dev server на 5173 проксирует /rest и /rpc в backend на 8000. Обычная production-схема использует один origin, отдельная CORS-конфигурация в приложении не предусмотрена

Основной путь вопроса:

```text
Workspace -> ApiClient.submitQuestion -> POST /answer-jobs/ -> AnswerQueue
Workspace -> polling GET /answer-jobs/ и GET /messages/ -> история и citations
```

[ag-ui.ts](../frontend/src/ag-ui.ts) с официальным HttpAgent сохранён для AG-UI интеграций и тестов. Текущий Workspace его для обычной отправки не вызывает. Backend AGUIAdapter/FunctionModel ждёт ту же очередь, а не запускает отдельную модель

## Компоненты и состояние

| Файл | Ответственность |
| --- | --- |
| App.tsx | JWT, identity, вход/выход, восстановление вкладки |
| Workspace.tsx | Проекты, чаты, материалы, отправка/stop, polling, локальные состояния |
| api.ts / types.ts | Typed REST, Bearer, pagination, безопасные ошибки |
| request-scope.ts | Session/selection guards против устаревших callbacks |
| ProjectSwitcher / ManagementDialog | Поиск проекта, создание, переименование, подтверждение удаления |
| Sources / PdfOriginal | Карточки источников, текст/PDF, blob lifecycle |
| Markdown / math | Markdown/KaTeX и нормализация формул |
| IndexingProgress | Процент, progressbar, счётчик chunks, подготовка/финализация |
| AnswerStatus / AnswerDuration | queued/pending и сохранённое время ответа |
| theme.tsx / ThemeToggle / themes.css | Системная/явная тема, хранение preference |
| Brand / branding.ts / Icon | Названия, общий логотип, локальные SVG |

Workspace сначала читает все проекты с pagination. После выбора ProjectWorkspace монтируется с ключом project UUID. Его unmount закрывает наблюдатели и сбрасывает локальные chat/messages/draft/source; серверные задачи остаются

При выборе чата RequestScope меняет selection, отменяется старое чтение history. Списки защищены sequence guards, чтобы поздний ответ не затёр более новое состояние. История и списки дочитываются страницами по 100 до total_count

После reload выбранный проект/чат не восстанавливаются из отдельного browser preference: используется первый проект и недавно обновлённый чат. Несохранённые черновики не хранятся между переключениями

## Отправка и очередь

«Новый чат» создаёт локальный пустой экран. Серверный чат создаётся при первом вопросе без title_text; begin_turn задаёт имя по вопросу. Начальная отправка защищена от двойного создания кнопкой/startingChat guard

QuestionRun содержит chatId, runId, messageId и вопрос. REST использует runId как client_request_identifier. Отправка не привязана к AbortSignal наблюдателя, чтобы переключение экрана не отменяло приём вопроса

Статусы активных ответов опрашиваются раз в секунду после предыдущего запроса, даже если очередь сейчас пуста. Для открытого активного чата перечитывается history. Завершение обновляет список чатов. Другой чат может отправить вопрос, текущий с queued/pending блокирует повторную отправку

«Остановить» вызывает stop endpoint; сервер отзывает lease, UI перечитывает статусы/историю. Потеря сети после отправки может означать успешный commit; frontend не создаёт автоматически новый request UUID для сомнительного запроса

«Повторить запрос» относится к локально сохранённой ошибке отправки: повтор использует прежний UUID/вопрос. Failed turn в истории показывает причину, но отдельной постоянной кнопки replay для каждого failed сообщения нет

Источники и assistant ID приходят из REST после сохранения. Поля временного optimistic UI не являются постоянной историей. Отдельные токены Ollama не отображаются потоково

## Материалы и управление

GUI принимает .txt/.md/.pdf, несколько файлов отправляются последовательно. Предел 100 MiB фиксирован в браузере; серверные лимиты настраиваются отдельно. .markdown поддерживается API, но проверка frontend его отклоняет

Список материалов опрашивается через 2.5 с после запроса только при queued/indexing/deleting. Процент берётся из API, не вычисляется из времени ожидания. Failed показывает код ошибки и retry; принудительного rebuild ready документа в интерфейсе нет

Удаление материала подтверждается у карточки; переименование/удаление чата - через меню, проекта - через ProjectSwitcher/ManagementDialog. Кнопка обновления материалов удалена; кнопки повторного чтения при ошибках отдельных разделов остаются

Поиск проектов выполняется в уже загруженном списке по названию. Shared workspace, перенос ресурсов между проектами и управление аккаунтом не реализованы

## Авторизация и безопасность отображения

JWT хранится в sessionStorage под study-rag.session и памяти вкладки. При старте проверяется /users/current/. Password не сохраняется. 401 и выход очищают сессию и размонтируют workspace; принятый серверный вопрос сам по себе не отменяется

Тема хранится отдельно в localStorage ragumentation.theme; system удаляет explicit preference и отслеживает prefers-color-scheme. Изменения localStorage синхронизируются между вкладками. HTML применяет тему до React, уменьшая вспышку при загрузке. При запрете storage выбор работает в памяти

Markdown использует react-markdown/remark-gfm/remark-math/rehype-katex. Raw HTML не исполняется, картинки заменяются alt, ссылки допускают http/https и открываются с noopener/noreferrer. KaTeX trust=false, ограничены макросы/размер, malformed формула не разрушает диалог

math normalization поддерживает dollar и скобочные delimiters, сохраняет code, чинит известные JSON escapes только внутри формул. Шрифты/CSS входят в build, CDN не нужен. Оригинальное содержимое сообщений в PostgreSQL этим renderer не переписывается

## PDF и время ответа

SourceDialog по умолчанию выбирает **текст**, включая PDF. Markdown p сохраняют whitespace/переносы. Исторический 404 chunk показывает excerpt из citation

PDF pane остаётся смонтированным, даже когда скрыт: authenticated fetch оригинала начинается при открытии диалога. PDF не перезагружается при смене вкладки. Bearer идёт только в header, viewer получает blob URL. На close запрос отменяется и URL освобождается

Начальная страница - страница citation. Prev/next управляют viewer, доступно открытие отдельно. Проверяется Content-Type application/pdf; ошибка прокси HTML не принимается за PDF. Нативный viewer зависит от браузера, точная область chunk на странице не подсвечивается

AnswerDuration форматирует response_duration_ms из БД: меньше секунды, секунды или минуты/секунды. Null скрывается, не заменяется нулём. Отсчёта истекшего времени во время pending и оценки очереди нет

## Оформление и границы

Светлая/тёмная/системная темы, фиолетовый основной акцент, нейтральные светлые тексты в тёмной теме. Типографические правки labels и статических подсказок не являются глобальной переработкой произвольного текста модели/пользовательских документов: Markdown renderer не удаляет в нём конечные точки и не заменяет всю пунктуацию

Composer: Enter отправить, Shift+Enter новая строка; кнопка отправки центрирована по высоте. На мобильном sidebar открывается меню. Dialog сохраняет focus, закрывается Escape/кнопкой и нажатием на фон

Результаты TypeScript/Vitest/build и браузерных проверок: [gui-validation.md](gui-validation.md). API: [contracts.md](contracts.md); пользовательские действия: [user-guide.md](user-guide.md)

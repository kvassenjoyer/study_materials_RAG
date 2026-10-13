# Локальные модели

Текущие adapters: [embeddings.py](../src/study_rag/adapters/embeddings.py), [generation.py](../src/study_rag/adapters/generation.py), [model_support.py](../src/study_rag/adapters/model_support.py), [tokenizer.py](../src/study_rag/adapters/tokenizer.py)

## Default: Ollama

| Назначение | Default |
| --- | --- |
| Embeddings | embeddinggemma:300m через /api/embed |
| Планирование, ответы, extraction, review, synonyms | qwen3.5:4b через /api/chat |
| Tokenizer chunk/generation budgets | Публичный HF Qwen/Qwen3.5-4B |
| Размерность индекса | 768, фиксируется Settings.vector_dimensions |
| Адрес Ollama | http://127.0.0.1:11434 |

```powershell
ollama pull embeddinggemma:300m
ollama pull qwen3.5:4b
ollama list
```

Запустите приложение Ollama или `ollama serve` в отдельном терминале. RAGumentation не запускает daemon и не скачивает Ollama-веса. Python скачивает только tokenizer Qwen в model_cache_directory. После кеширования можно включить HF_HUB_OFFLINE=1 для tokenizer

Идентификаторы моделей должны совпадать с настройками. Startup/readiness проверяет модель через /api/show и её capabilities, а не выполняет пробный ответ. Наличие модели не гарантирует скорость или качество; размер/квантизация и GPU offload зависят от Ollama и установленного артефакта. Старые macOS timings не являются характеристиками Windows

## Embedding input и chunk budgets

Оба backend добавляют prefix ровно один раз:

```text
task: search result | query: вопрос
title: имя файла | text: текст фрагмента
```

Title очищается от pipe/лишних пробелов и ограничивается 256 UTF-8 байтами без разрезания символа. Default chunks: до 512 content tokens, overlap 64; страницы PDF не смешиваются

В Ollama profile tokenizer Qwen **не является точным tokenizer Gemma**. Поэтому дополнительно действуют content byte limit 1536, formatted byte limit 1900 и проверка formatted token budget 2048. /api/embed вызывается с truncate=false: runtime не должен скрыто обрезать вход

Вопрос также проходит embedding formatted-input проверки. Допустимые HTTP 4000 символов не гарантируют, что очень длинный вопрос поместится в embedding budget

Adapters проверяют количество векторов, конечность координат, ненулевую норму и размерность, затем L2-нормализуют. Adapters поддерживают размеры 128/256/512/768, но приложение создаёт только 768-мерный индекс: настройки размерности в .env нет

## Optional Hugging Face backend

Для `google/embeddinggemma-300m` нужны доступ к файлам модели и принятие её условий на Hugging Face, если доступ запрашивается. Актуальные условия проверяются пользователем при настройке; документ не фиксирует состояние внешнего gating навсегда

```powershell
uv sync --locked --group dev --extra huggingface
uv run --no-sync hf auth login
```

В .env установите STUDY_RAG_EMBEDDING_BACKEND=huggingface, при необходимости STUDY_RAG_EMBEDDING_DEVICE=cpu/mps/cuda. auto выбирает MPS, затем CUDA, затем CPU по доступности PyTorch; недоступное явно выбранное устройство даёт ошибку

HF использует Gemma tokenizer для chunk/embedding input, а Qwen tokenizer отдельно для генерации. Модель загружается через Sentence Transformers с trust_remote_code=false и float32. Полный formatted input проверяется до encode, truncation=false; default prompt Sentence Transformers отключён, чтобы prefix не добавлялся дважды

HF inference вынесен из event loop и сериализован. Реальный запуск HF-весов не подтверждён текущими обычными тестами: они используют injected encoder/tokenizer doubles. Наличие optional extra в проекте не означает установленный HF runtime на каждой машине

## Профиль индекса

Settings вычисляет embedding_profile из backend, model ID, 768 dimensions, tokenizer, chunk tokens/overlap, byte limits для Ollama и версии prefix policy. Profile digest - первые 16 hex-символов SHA-256 сериализованных настроек

Профиль не содержит generation model, similarity threshold, parser version или digest самих весов. Поэтому смена parser требует явного rebuild; замена весов под прежним тегом не обнаруживается автоматически как смена profile

Смена backend/model/tokenizer/chunk settings меняет profile. Заново загрузите материалы: одинаковый файл получит новый document UUID и отдельный индекс. Старые документы остаются в списке, но не участвуют в поиске нового profile. Retry/rebuild старого profile не переводит его в новый: worker возвращает embedding_profile_changed

## Генерация и повторы

Chat payload задаёт stream=false, think=false, JSON schema, temperature=0.1, num_ctx=16384 и num_predict=2048 по умолчанию. Обычный grounded JSON ограничен 16000 символами и 48 labels; общий ответ - 4000 символами. Ответ обзора собирается кодом из извлечённых пунктов

История ограничена числом сообщений application (default 8) и 2048 tokens внутри generator. Бюджет prompt учитывает system/user и резерв шаблона; при переполнении убираются целые старые сообщения, затем последние источники. Не помещающийся вопрос вызывает question_budget_exceeded

При done=true/done_reason=length выполняется один новый запрос с теми же prompt/schema и бюджетом min(2 * output_tokens, доступный остаток контекста), по умолчанию до 4096. Частичный ответ отбрасывается. Повторный length - model_response_truncated, другое незавершение - incomplete_model_response

JSON и labels проверяются. Неизвестные источники отклоняются; отсутствующие inline markers для допустимых declared labels добавляются детерминированно. General answer не может объявлять citations

Ollama transport повторяет сетевые ошибки и HTTP 429/500/502/503/504 до трёх попыток с backoff/jitter; другие HTTP 4xx не повторяются. Generation operation budget, включая ожидание lock, - min(configured timeout, 120 с), embedding attempt timeout - min(generation timeout / 3, 40 с). Общий бюджет обработки turn - 240 с

Смысловая review и synonyms используют ту же модель, увеличивая число вызовов. См. [адаптивный поиск](adaptive-search.md) и [конфигурацию](configuration.md)

## Проверка

Unit tests покрывают prefixes, no-truncation, dimensions, normalization, device selection, budgets, JSON/labels, length retry и review. Они не измеряют качество на учебниках

`scripts/smoke_local.py` проверяет настоящий сервер на собственных RU/EN материалах, численный follow-up, history, replay, tenant isolation и ответы без источников. Текущий статус результатов и границы: [backend-validation.md](backend-validation.md)

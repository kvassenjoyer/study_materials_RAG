import type { AuthToken, ChatRecord, ChunkRecord, DocumentRecord, Identity, MessageRecord, Page, ProjectRecord } from './types';

const base = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
if (base && (!base.startsWith('/') || base.startsWith('//') || /[?#\\]/.test(base))) {
  throw new Error('VITE_API_BASE_URL must be a same-origin path');
}
export const apiUrl = (path: string) => `${base}${path}`;
const prefix = '/rest/study-rag';

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) { super(message); }
}

const errorTranslations: Record<string, string> = {
  invalid_credentials: 'Неверный логин или пароль', login_already_registered: 'Этот логин уже занят',
  account_already_exists: 'Этот логин уже занят', invalid_question: 'Введите вопрос длиной до 4000 символов',
  document_too_large: 'Файл превышает допустимый размер', unsupported_document: 'Поддерживаются файлы TXT, MD и PDF',
  unsupported_file_type: 'Поддерживаются файлы TXT, MD и PDF', chat_busy: 'В этом чате уже готовится ответ. Подождите и обновите историю',
  unsupported_document_format: 'Поддерживаются файлы TXT, MD и PDF', unsupported_document_type: 'Не удалось определить формат документа',
  invalid_login_name: 'Логин должен содержать 3-64 символа: буквы, цифры, точку, дефис или подчёркивание',
  invalid_password_length: 'Пароль должен содержать от 12 до 128 символов',
  invalid_project_title: 'Введите название проекта длиной до 200 символов',
  project_not_found: 'Проект больше недоступен',
  invalid_chat_title: 'Введите название чата длиной до 200 символов',
  empty_document: 'Выбранный файл пуст', invalid_utf8: 'Сохраните текстовый файл в кодировке UTF-8',
  invalid_pdf: 'Не удалось прочитать PDF. Проверьте, что файл не повреждён', encrypted_pdf: 'PDF с паролем не поддерживается',
  document_not_pdf: 'Для этого материала доступен текстовый фрагмент',
  invalid_pdf_original: 'Не удалось загрузить оригинал PDF. Можно открыть текстовый фрагмент',
  stored_file_not_found: 'Оригинал файла больше недоступен. Можно открыть текстовый фрагмент',
  too_many_pdf_pages: 'В PDF слишком много страниц. Разделите его на несколько файлов',
  pdf_page_too_large: 'Страница PDF слишком большая для обработки', extracted_text_too_large: 'В документе слишком много текста. Разделите его на несколько файлов',
  no_extractable_text: 'В документе нет извлекаемого текста. Для PDF требуется текстовый слой',
  answer_timeout: 'Ответ занял слишком много времени. Можно повторить запрос',
  answer_generation_failed: 'Не удалось подготовить ответ. Попробуйте повторить запрос',
  model_response_truncated: 'Ответ оказался слишком длинным и был обрезан. Автоматический повтор не помог. Разбейте вопрос на несколько частей',
  incomplete_model_response: 'Модель не завершила ответ. Попробуйте повторить запрос или уточнить вопрос',
  model_request_timeout: 'Локальная модель не успела подготовить ответ. Попробуйте задать более короткий вопрос',
  model_unavailable: 'Ollama не смогла выполнить запрос. Проверьте, что она запущена, и повторите запрос',
  generation_model_unavailable: 'Модель для ответов недоступна в Ollama. Проверьте, что она установлена',
  invalid_model_response: 'Модель вернула некорректный ответ. Попробуйте повторить запрос',
  invalid_grounded_answer: 'Не удалось проверить формат ответа по материалам. Попробуйте повторить запрос',
  invalid_general_answer: 'Не удалось проверить формат ответа модели. Попробуйте повторить запрос',
  invalid_question_rewrite: 'Модель не смогла уточнить вопрос по истории чата. Сформулируйте вопрос с указанием темы',
  invalid_search_plan: 'Модель не смогла спланировать поиск. Попробуйте уточнить тему вопроса',
  invalid_evidence_extraction: 'Не удалось проверить сведения из найденных разделов. Попробуйте повторить запрос',
  invalid_evidence_review: 'Не удалось проверить смысл найденных понятий. Попробуйте повторить запрос',
  unknown_answer_citation: 'Модель сослалась на источник, которого нет среди найденных материалов. Попробуйте повторить запрос',
  unsupported_answer: 'Модель не указала источники для ответа по материалам. Попробуйте уточнить вопрос',
  question_budget_exceeded: 'Вопрос не помещается в контекст модели. Сократите его или разделите на несколько вопросов',
  answer_cancelled: 'Подготовка ответа была прервана. Можно повторить запрос',
  answer_stopped: 'Вы остановили подготовку ответа',
  turn_lease_expired: 'Подготовка ответа была прервана по времени. Можно повторить запрос',
  no_text_extracted: 'Не удалось извлечь текст. Для PDF требуется текстовый слой',
};

export function documentFailureText(code: string | null, message: string | null): string {
  return (code && errorTranslations[code]) || message?.replace(/(?<!\.)\.\s*$/, '') || 'Не удалось обработать материал';
}

export function turnFailureText(code: string | null | undefined): string {
  return (code && errorTranslations[code]) || 'Ответ не был получен. Попробуйте повторить запрос';
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (errorTranslations[error.code]) return errorTranslations[error.code];
    if (error.status === 401) return 'Сессия истекла. Войдите ещё раз';
    if (error.status === 404) return 'Материал или чат больше недоступен';
    if (error.status === 409) return 'Состояние изменилось. Обновите данные и попробуйте снова';
    if (error.status === 413) return 'Файл превышает допустимый размер';
    if (error.status === 415) return 'Поддерживаются файлы TXT, MD и PDF';
    if (error.status === 422 || error.status === 400) return 'Проверьте введённые данные и повторите запрос';
    if (error.status >= 500) return 'Сервис временно недоступен. Попробуйте позже';
  }
  return 'Не удалось связаться с сервисом. Проверьте соединение и попробуйте снова';
}

export function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

export class ApiClient {
  constructor(private readonly token: string | null, private readonly unauthorized: () => void = () => {},
    private readonly fetcher: typeof fetch = (...args) => fetch(...args)) {}

  async transport(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const response = await this.fetcher(input, init);
    if (response.ok) return response;
    if (response.status === 401) this.unauthorized();
    let code = '';
    try {
      const body: unknown = await response.json();
      if (body && typeof body === 'object' && 'error_code' in body && typeof body.error_code === 'string') code = body.error_code;
    } catch { /* Non-JSON proxy failures use the same safe status fallback. */ }
    throw new ApiError(response.status, code, 'Request failed');
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (this.token) headers.set('Authorization', `Bearer ${this.token}`);
    if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    const response = await this.transport(apiUrl(`${prefix}${path}`), { ...init, headers });
    return response.status === 204 ? undefined as T : await response.json() as T;
  }

  async all<T>(path: string, signal?: AbortSignal): Promise<T[]> {
    const records: T[] = [];
    let offset = 0;
    while (true) {
      const separator = path.includes('?') ? '&' : '?';
      const page = await this.request<Page<T>>(`${path}${separator}limit=100&offset=${offset}`, { signal });
      records.push(...page.items);
      offset += page.items.length;
      if (offset >= page.total_count) return records;
      if (!page.items.length) throw new ApiError(503, 'incomplete_page', 'Incomplete page');
    }
  }

  authenticate(login: string, password: string, register: boolean, signal?: AbortSignal) {
    return this.request<AuthToken>(register ? '/users/' : '/sessions/', {
      method: 'POST', body: JSON.stringify({ login_name: login, password_text: password }), signal,
    });
  }
  identity(signal?: AbortSignal) { return this.request<Identity>('/users/current/', { signal }); }
  projects(signal?: AbortSignal) { return this.all<ProjectRecord>('/projects/', signal); }
  createProject(title: string, signal?: AbortSignal) {
    return this.request<ProjectRecord>('/projects/', { method: 'POST', body: JSON.stringify({ title_text: title }), signal });
  }
  renameProject(id: string, title: string, signal?: AbortSignal) {
    return this.request<ProjectRecord>(`/projects/${encodeURIComponent(id)}/`, { method: 'PATCH', body: JSON.stringify({ title_text: title }), signal });
  }
  deleteProject(id: string, signal?: AbortSignal) {
    return this.request<void>(`/projects/${encodeURIComponent(id)}/`, { method: 'DELETE', signal });
  }
  renameChat(id: string, title: string, signal?: AbortSignal) {
    return this.request<ChatRecord>(`/chats/${encodeURIComponent(id)}/`, { method: 'PATCH', body: JSON.stringify({ title_text: title }), signal });
  }
  deleteChat(id: string, signal?: AbortSignal) {
    return this.request<void>(`/chats/${encodeURIComponent(id)}/`, { method: 'DELETE', signal });
  }
  chats(projectId: string, signal?: AbortSignal) {
    return this.all<ChatRecord>(`/chats/?project_identifier=${encodeURIComponent(projectId)}`, signal);
  }
  messages(chatId: string, signal?: AbortSignal) {
    return this.all<MessageRecord>(`/messages/?chat_identifier=${encodeURIComponent(chatId)}`, signal);
  }
  createChat(projectId: string, signal?: AbortSignal) {
    return this.request<ChatRecord>('/chats/', { method: 'POST', body: JSON.stringify({ project_identifier: projectId }), signal });
  }
  documents(projectId: string, signal?: AbortSignal) {
    return this.all<DocumentRecord>(`/documents/?project_identifier=${encodeURIComponent(projectId)}`, signal);
  }
  upload(file: File, projectId: string, signal?: AbortSignal) {
    const body = new FormData(); body.append('file', file);
    return this.request<DocumentRecord>(`/documents/?project_identifier=${encodeURIComponent(projectId)}`, { method: 'POST', body, signal });
  }
  retryDocument(id: string, signal?: AbortSignal) {
    return this.request<DocumentRecord>('/indexing-jobs/', { method: 'POST', body: JSON.stringify({ document_identifier: id }), signal });
  }
  deleteDocument(id: string, signal?: AbortSignal) {
    return this.request<DocumentRecord>(`/documents/${encodeURIComponent(id)}/`, { method: 'DELETE', signal });
  }
  chunk(id: string, signal?: AbortSignal) { return this.request<ChunkRecord>(`/chunks/${encodeURIComponent(id)}/`, { signal }); }
}

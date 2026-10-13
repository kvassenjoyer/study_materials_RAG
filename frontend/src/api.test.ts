import { describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiError, errorText, turnFailureText } from './api';

describe('REST transport', () => {
  it('loads original PDFs with bearer headers and page metadata without putting credentials in the URL', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('%PDF-original', { headers: { 'Content-Type': 'application/pdf', 'X-PDF-Page-Count': '2' } }));
    const result = await new ApiClient('test-bearer', () => {}, fetcher).originalPdf('document-id');
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe('/rest/study-rag/documents/document-id/original/');
    expect(String(url)).not.toContain('test-bearer');
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer test-bearer');
    expect(result.pageCount).toBe(2);
    expect(await result.blob.text()).toBe('%PDF-original');
  });

  it('rejects a proxy HTML page instead of displaying it as the PDF original', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('<html>Error</html>', { headers: { 'Content-Type': 'text/html' } }));
    await expect(new ApiClient('test-bearer', () => {}, fetcher).originalPdf('document-id')).rejects.toMatchObject({ code: 'invalid_pdf_original' });
  });

  it.each([true, false])('authenticates with a login JSON field (register: %s)', async register => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ access_token: 'test-token', expires_in: 3600, token_type: 'bearer' }));
    await new ApiClient(null, () => {}, fetcher).authenticate('student_123', 'fixture-password', register);
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe(`/rest/study-rag/${register ? 'users' : 'sessions'}/`);
    expect(JSON.parse(String(init?.body))).toEqual({ login_name: 'student_123', password_text: 'fixture-password' });
  });

  it.each([
    ['invalid_credentials', 'Неверный логин или пароль'],
    ['login_already_registered', 'Этот логин уже занят'],
  ])('explains login errors (%s)', (code, text) => {
    expect(errorText(new ApiError(400, code, 'private backend details'))).toBe(text);
  });

  it('reads every history page and sends bearer auth in headers only', async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ items: [{ message_identifier: 'first' }], total_count: 2, offset: 0, limit: 100 }))
      .mockResolvedValueOnce(Response.json({ items: [{ message_identifier: 'last' }], total_count: 2, offset: 1, limit: 100 }));
    const client = new ApiClient('test-bearer', () => {}, fetcher);
    const history = await client.messages('chat-id');
    expect(history.map(message => message.message_identifier)).toEqual(['first', 'last']);
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      '/rest/study-rag/messages/?chat_identifier=chat-id&limit=100&offset=0',
      '/rest/study-rag/messages/?chat_identifier=chat-id&limit=100&offset=1',
    ]);
    for (const [url, init] of fetcher.mock.calls) {
      expect(String(url)).not.toContain('test-bearer');
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer test-bearer');
    }
  });

  it('expires the session on 401 and avoids returning private error details', async () => {
    const expired = vi.fn();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error_code: 'invalid_access_token', message_text: 'private backend details' }, { status: 401 }));
    const client = new ApiClient('test-bearer', expired, fetcher);
    let failure: unknown;
    try { await client.identity(); } catch (error) { failure = error; }
    expect(expired).toHaveBeenCalledOnce();
    expect(failure).toBeInstanceOf(ApiError);
    expect(errorText(failure)).toBe('Сессия истекла. Войдите ещё раз');
  });

  it('uploads with multipart field file without forcing a JSON content type', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ state_name: 'queued' }));
    await new ApiClient('test-bearer', () => {}, fetcher).upload(new File(['notes'], 'lecture.md', { type: 'text/markdown' }), 'project-id');
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe('/rest/study-rag/documents/?project_identifier=project-id');
    expect(new Headers(init?.headers).has('Content-Type')).toBe(false);
    expect(init?.body).toBeInstanceOf(FormData);
    expect((init?.body as FormData).get('file')).toBeInstanceOf(File);
  });

  it('does not spin forever on an inconsistent empty page', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ items: [], total_count: 3, offset: 0, limit: 100 }));
    await expect(new ApiClient(null, () => {}, fetcher).chats('project-id')).rejects.toMatchObject({ code: 'incomplete_page' });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it('handles empty deletion responses and creates chats without a manual title', async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(Response.json({ chat_identifier: 'new-chat' }));
    const client = new ApiClient('test-bearer', () => {}, fetcher);
    await expect(client.deleteChat('chat-id')).resolves.toBeUndefined();
    await expect(client.deleteProject('project-id')).resolves.toBeUndefined();
    await client.createChat('project-id');
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual({ project_identifier: 'project-id' });
  });
});

describe('answer failures', () => {
  it('distinguishes explicit stop from a connection interruption', () => {
    expect(turnFailureText('answer_stopped')).toBe('Вы остановили подготовку ответа');
    expect(turnFailureText('answer_cancelled')).not.toBe(turnFailureText('answer_stopped'));
  });
  it.each(['model_response_truncated', 'incomplete_model_response', 'model_request_timeout', 'model_unavailable', 'unknown_answer_citation', 'invalid_evidence_review'])('shows the same meaningful reason for a live and persisted %s failure', code => {
    const message = errorText(new ApiError(503, code, 'private backend details'));
    expect(message).toBe(turnFailureText(code));
    expect(message).not.toContain('private backend details');
    expect(message).not.toBe('Сервис временно недоступен. Попробуйте позже');
  });

  it('explains truncation after retry and suggests splitting the question', () => {
    expect(turnFailureText('model_response_truncated')).toContain('Автоматический повтор не помог');
    expect(turnFailureText('model_response_truncated')).toContain('Разбейте вопрос');
  });

  it('uses a safe fallback for missing and unknown persisted error codes', () => {
    expect(turnFailureText(null)).toBe(turnFailureText('unknown_internal_failure'));
    expect(turnFailureText(undefined)).toContain('Попробуйте повторить запрос');
  });
});

describe('background answers', () => {
  it('submits once with a stable id, observes jobs and explicitly stops by turn id', async () => {
    const accepted = { turn_identifier: 'turn-id', chat_identifier: 'chat-id', project_identifier: 'project-id', status_name: 'queued' };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(accepted, { status: 202 }))
      .mockResolvedValueOnce(Response.json([accepted]))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const api = new ApiClient('test-bearer', () => {}, fetcher);
    const request = { chatId: 'chat-id', runId: 'stable-request-id', messageId: 'message-id', question: 'Explain queues' };
    expect(await api.submitQuestion(request)).toEqual(accepted);
    expect(await api.activeAnswers()).toEqual([accepted]);
    await expect(api.stopAnswer('turn-id')).resolves.toBeUndefined();
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['/rest/study-rag/answer-jobs/', '/rest/study-rag/answer-jobs/', '/rest/study-rag/answer-jobs/turn-id/stop/']);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ chat_identifier: 'chat-id', client_request_identifier: 'stable-request-id', question_text: 'Explain queues' });
    // Navigation cannot abort an accepted submission; stop is a distinct authenticated POST.
    expect(fetcher.mock.calls[0][1]?.signal).toBeUndefined();
    expect(fetcher.mock.calls[2][1]?.method).toBe('POST');
    expect(new Headers(fetcher.mock.calls[2][1]?.headers).get('Authorization')).toBe('Bearer test-bearer');
  });
});

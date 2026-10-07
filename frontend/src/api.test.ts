import { describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiError, errorText } from './api';

describe('REST transport', () => {
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
    expect(errorText(failure)).toBe('Сессия истекла. Войдите ещё раз.');
  });

  it('uploads with multipart field file without forcing a JSON content type', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ state_name: 'queued' }));
    await new ApiClient('test-bearer', () => {}, fetcher).upload(new File(['notes'], 'lecture.md', { type: 'text/markdown' }));
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe('/rest/study-rag/documents/');
    expect(new Headers(init?.headers).has('Content-Type')).toBe(false);
    expect(init?.body).toBeInstanceOf(FormData);
    expect((init?.body as FormData).get('file')).toBeInstanceOf(File);
  });

  it('does not spin forever on an inconsistent empty page', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ items: [], total_count: 3, offset: 0, limit: 100 }));
    await expect(new ApiClient(null, () => {}, fetcher).chats()).rejects.toMatchObject({ code: 'incomplete_page' });
    expect(fetcher).toHaveBeenCalledOnce();
  });
});

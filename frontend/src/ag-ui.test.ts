import { describe, expect, it, vi } from 'vitest';
import { ApiClient } from './api';
import { startQuestion } from './ag-ui';
import { RequestScope } from './request-scope';
import type { QuestionRun } from './types';

const run: QuestionRun = {
  chatId: '22222222-2222-4222-8222-222222222222', runId: '33333333-3333-4333-8333-333333333333',
  messageId: '44444444-4444-4444-8444-444444444444', question: 'Что такое градиент?',
};
const event = (value: object) => `data: ${JSON.stringify(value)}\n\n`;
const events = [
  { type: 'RUN_STARTED', threadId: run.chatId, runId: run.runId },
  { type: 'TEXT_MESSAGE_START', messageId: 'assistant-id', role: 'assistant' },
  { type: 'TEXT_MESSAGE_CONTENT', messageId: 'assistant-id', delta: 'Градиент — вектор частных производных. [S1]' },
  { type: 'TEXT_MESSAGE_END', messageId: 'assistant-id' },
  { type: 'RUN_FINISHED', threadId: run.chatId, runId: run.runId },
];
const response = (values = events) => new Response(values.map(event).join(''), { headers: { 'Content-Type': 'text/event-stream' } });

describe('official HttpAgent integration', () => {
  it('sends one latest question and hydrates persisted messages only after RUN_FINISHED', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response());
    const text = vi.fn(); const hydrate = vi.fn(async () => {});
    await startQuestion(new ApiClient('test-bearer', () => {}, fetcher), 'test-bearer', run, { isCurrent: () => true, onText: text, onFinished: hydrate }).completion;
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe('/rpc/study-rag/agent/');
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer test-bearer');
    expect(JSON.parse(String(init?.body))).toMatchObject({
      threadId: run.chatId, runId: run.runId, protocolVersion: '1.0',
      messages: [{ id: run.messageId, role: 'user', content: run.question }], state: {}, tools: [], context: [], forwardedProps: {},
    });
    expect(text).toHaveBeenCalledWith(events[2].delta);
    expect(hydrate).toHaveBeenCalledOnce();
    expect(text.mock.invocationCallOrder[0]).toBeLessThan(hydrate.mock.invocationCallOrder[0]);
  });

  it('keeps the same request UUID when a question is retried', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => response());
    const callbacks = { isCurrent: () => true, onText: () => {}, onFinished: async () => {} };
    const client = new ApiClient('test-bearer', () => {}, fetcher);
    await startQuestion(client, 'test-bearer', run, callbacks).completion;
    await startQuestion(client, 'test-bearer', run, callbacks).completion;
    const inputs = fetcher.mock.calls.map(([, init]) => JSON.parse(String(init?.body)));
    expect(inputs[0].runId).toBe(inputs[1].runId);
    expect(inputs[0].messages).toEqual(inputs[1].messages);
  });

  it('rejects a stream that never reaches completion', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(events.slice(0, -1)));
    const hydrate = vi.fn(async () => {});
    await expect(startQuestion(new ApiClient('token', () => {}, fetcher), 'token', run,
      { isCurrent: () => true, onText: () => {}, onFinished: hydrate }).completion).rejects.toThrow();
    expect(hydrate).not.toHaveBeenCalled();
  });

  it('does not apply text or hydration to another chat after a delayed response', async () => {
    let resolveResponse!: (value: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise(resolve => { resolveResponse = resolve; }));
    const scope = new RequestScope(); scope.select(run.chatId);
    const text = vi.fn(); const hydrate = vi.fn(async () => {});
    const active = startQuestion(new ApiClient('token', () => {}, fetcher), 'token', run,
      { isCurrent: scope.selection(), onText: text, onFinished: hydrate });
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    scope.select('another-chat'); resolveResponse(response()); await active.completion;
    expect(text).not.toHaveBeenCalled(); expect(hydrate).not.toHaveBeenCalled();
  });

  it('does not start a request after logout before the SDK has loaded', async () => {
    const fetcher = vi.fn<typeof fetch>(); const scope = new RequestScope(); scope.select(run.chatId);
    const active = startQuestion(new ApiClient('old-token', () => {}, fetcher), 'old-token', run,
      { isCurrent: scope.selection(), onText: () => {}, onFinished: async () => {} });
    scope.dispose();
    await expect(active.completion).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetcher).not.toHaveBeenCalled();
  });
});

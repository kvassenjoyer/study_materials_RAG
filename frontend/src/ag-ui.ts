import type { HttpAgent } from '@ag-ui/client';
import type { ApiClient } from './api';
import { apiUrl, ApiError } from './api';
import type { QuestionRun } from './types';

export interface AgentCallbacks { isCurrent: () => boolean; onText: (text: string) => void; onFinished: () => Promise<void> }

export function startQuestion(api: ApiClient, token: string, run: QuestionRun, callbacks: AgentCallbacks) {
  let agent: HttpAgent | null = null;
  let cancelled = false;
  const completion = import('@ag-ui/client').then(({ HttpAgent }) => {
    if (cancelled || !callbacks.isCurrent()) throw new DOMException('Run cancelled', 'AbortError');
    agent = new HttpAgent({
      url: apiUrl('/rpc/study-rag/agent/'), threadId: run.chatId,
      headers: { Authorization: `Bearer ${token}` },
      initialMessages: [{ id: run.messageId, role: 'user', content: run.question }],
      initialState: {}, fetch: (input, init) => api.transport(input, init),
    });
    let finished = false;
    let runError: ApiError | null = null;
    return agent.runAgent({ runId: run.runId, tools: [], context: [], forwardedProps: {} }, {
      onTextMessageContentEvent: ({ event, textMessageBuffer }) => {
        if (callbacks.isCurrent()) callbacks.onText(textMessageBuffer + event.delta);
      },
      onRunErrorEvent: ({ event }) => { runError = new ApiError(503, event.code ?? '', 'Agent run failed'); },
      onRunFinishedEvent: async () => {
        finished = true;
        if (callbacks.isCurrent()) await callbacks.onFinished();
      },
    }).then(() => {
      if (runError) throw runError;
      if (!finished) throw new ApiError(503, 'incomplete_run', 'Agent stream ended without completion');
    });
  });
  return { completion, abort: () => { cancelled = true; agent?.abortRun(); } };
}

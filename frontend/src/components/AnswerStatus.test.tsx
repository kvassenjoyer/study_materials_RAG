import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AnswerStatus } from './AnswerStatus';

describe('background answer status', () => {
  it.each([['queued', 'В очереди'], ['pending', 'Готовлю ответ…']] as const)('labels %s in the chat list', (status, label) => {
    const output = renderToStaticMarkup(<AnswerStatus status={status}/>);
    expect(output).toContain(label);
    expect(output).toContain('role="status"');
  });
});

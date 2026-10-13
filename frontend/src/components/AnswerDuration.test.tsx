import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AnswerDuration } from './AnswerDuration';

describe('answer duration', () => {
  it.each([
    [0, 'Ответ менее чем за 1 с'],
    [999, 'Ответ менее чем за 1 с'],
    [1500, 'Ответ за 2 с'],
    [59_600, 'Ответ за 1 мин'],
    [225_046, 'Ответ за 3 мин 45 с'],
  ])('formats measured %s milliseconds without losing minute boundaries', (milliseconds, text) => {
    const output = renderToStaticMarkup(<AnswerDuration milliseconds={milliseconds}/>);
    expect(output).toContain(text);
    expect(output).toContain('включая поиск по материалам');
  });

  it.each([undefined, null, -1, Number.NaN, Number.POSITIVE_INFINITY])('hides missing or invalid timing %s', milliseconds => {
    expect(renderToStaticMarkup(<AnswerDuration milliseconds={milliseconds}/>)).toBe('');
  });
});

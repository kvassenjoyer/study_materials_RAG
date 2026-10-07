import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { Markdown } from './Markdown';

describe('untrusted Markdown', () => {
  it('renders text and code without executing HTML or loading remote images', () => {
    const output = renderToStaticMarkup(<Markdown text={'# Конспект\n\n<script>alert(1)</script>\n\n![tracking](https://attacker.example/track)\n\n```html\n<img onerror="alert(1)">\n```'}/>);
    expect(output).toContain('<h1>Конспект</h1>');
    expect(output).not.toContain('<script'); expect(output).not.toContain('<img');
    expect(output).not.toContain('https://attacker.example');
    expect(output).toContain('&lt;img');
  });

  it('allows web links and drops executable or relative URL schemes', () => {
    const output = renderToStaticMarkup(<Markdown text={'[bad](javascript:alert%281%29) [relative](/rest/private/) [web](https://example.com/lesson)'}/>);
    expect(output).not.toContain('javascript:'); expect(output).not.toContain('/rest/private/');
    expect(output).toContain('href="https://example.com/lesson"');
    expect(output).toContain('rel="noopener noreferrer"');
  });
});

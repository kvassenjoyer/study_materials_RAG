import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { Markdown } from './Markdown';
import { normalizeMathDelimiters } from './math';

describe('untrusted Markdown', () => {
  it('repairs the saved 123 answer where JSON decoded times as a tab', () => {
    const text = '$1 \times 10^2 + 2 \times 10^1 + 3 \times 10^0 = 100 + 20 + 3$';
    expect(text).toContain('\t');
    expect(normalizeMathDelimiters(text)).toBe(String.raw`$1 \times 10^2 + 2 \times 10^1 + 3 \times 10^0 = 100 + 20 + 3$`);
    const output = renderToStaticMarkup(<Markdown text={text}/>);
    expect(output).toContain('×');
    expect(output).not.toContain('katex-error');
    expect(output).not.toContain('<mi>i</mi>');
  });

  it('repairs JSON control escapes inside all supported math delimiters', () => {
    const text = '$\frac{1}{2} + \beta$ and \\(\theta + \rho\\)\n\n\\[\nabla f \\]';
    const normalized = normalizeMathDelimiters(text);
    for (const command of ['frac', 'beta', 'theta', 'rho', 'nabla']) expect(normalized).toContain(`\\${command}`);
    expect(renderToStaticMarkup(<Markdown text={text}/>)).not.toContain('katex-error');
  });

  it('preserves ordinary whitespace, escaped dollars and code containing damaged TeX', () => {
    const text = 'A\timetable\nnext line\rreally\n\n`$1 \times 2$`\n\n```latex\n$\frac{1}{2}$\n```';
    expect(normalizeMathDelimiters(text)).toBe(text);
    expect(normalizeMathDelimiters(String.raw`Price: \$5 and \$10. Math: $a \times b$.`))
      .toBe(String.raw`Price: \$5 and \$10. Math: $a \times b$.`);
    expect(normalizeMathDelimiters('$a\t+ b\n+ c$')).toBe('$a\t+ b\n+ c$');
  });

  it('typesets inline and display TeX in the shared message/source renderer', () => {
    const output = renderToStaticMarkup(<Markdown text={String.raw`Inline $x^2$ and \(\alpha + \beta\).

$$
\frac{a}{b}
$$

\[\int_0^1 x\,dx\]`}/>);
    expect((output.match(/class="katex"/g) ?? []).length).toBe(4);
    expect((output.match(/class="katex-display"/g) ?? []).length).toBe(2);
    expect(output).toContain('<math');
    expect(output).not.toContain('katex-error');
  });

  it('preserves TeX examples in inline code and both kinds of fenced code', () => {
    const text = '`\\(x\\)`\n\n```latex\n\\[x\\]\n```\n\n~~~latex\n\\(y\\)\n~~~';
    expect(normalizeMathDelimiters(text)).toBe(text);
    expect(renderToStaticMarkup(<Markdown text={text}/>)).not.toContain('class="katex"');
    expect(normalizeMathDelimiters('```latex\n\\(unfinished\\)')).toBe('```latex\n\\(unfinished\\)');
  });

  it('shows malformed formulas safely and blocks trusted TeX URL/image commands', () => {
    const output = renderToStaticMarkup(<Markdown text={String.raw`$\notARealMacro{x}$

$\href{javascript:alert(1)}{click}$

$\includegraphics{https://attacker.example/track}$`}/>);
    expect(output).toContain('\\notARealMacro');
    expect(output).not.toContain('<img');
    expect(output).not.toContain('href="javascript:');
  });

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

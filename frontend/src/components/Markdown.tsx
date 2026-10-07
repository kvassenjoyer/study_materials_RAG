import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export function safeWebUrl(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : '';
  } catch { return ''; }
}

export function Markdown({ text }: { text: string }) {
  return <div className="markdown"><ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} urlTransform={safeWebUrl}
    components={{
      a: ({ href, children }) => href
        ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
        : <span>{children}</span>,
      img: ({ alt }) => <span className="image-placeholder">{alt || 'Изображение'}</span>,
    }}>{text}</ReactMarkdown></div>;
}

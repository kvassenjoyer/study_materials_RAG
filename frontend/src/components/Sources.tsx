import { useEffect, useRef, useState } from 'react';
import type { ApiClient } from '../api';
import { ApiError, errorText, isAbort } from '../api';
import type { ChunkRecord, Citation } from '../types';
import { Icon } from './Icon';
import { Markdown } from './Markdown';
import { PdfOriginal } from './PdfOriginal';

export function pageText(start: number | null, end: number | null): string {
  if (start === null) return 'Фрагмент текста';
  return end !== null && end !== start ? `Страницы ${start}-${end}` : `Страница ${start}`;
}

export function Sources({ citations, onOpen }: { citations: Citation[]; onOpen: (citation: Citation) => void }) {
  if (!citations.length) return null;
  return <div className="sources"><p className="section-label">Источники <span>{citations.length}</span></p>
    <div className="source-grid">{citations.map(citation => <button className="source-card" key={citation.chunk_identifier}
      onClick={() => onOpen(citation)} aria-label={`Открыть фрагмент ${citation.source_label}: ${citation.filename_text}`}>
      <div className="source-heading"><span className="source-label">{citation.source_label}</span>
        <span className="source-filename">{citation.filename_text}</span><Icon name="chevron" size={16}/></div>
      <span className="source-pages">{pageText(citation.page_start, citation.page_end)}</span>
      <span className="source-excerpt">{citation.excerpt_text}</span>
      <span className="source-action">Открыть фрагмент</span>
    </button>)}</div>
  </div>;
}

export function SourceDialog({ api, citation, onClose }: { api: ApiClient; citation: Citation; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [chunk, setChunk] = useState<ChunkRecord | null>(null);
  const [error, setError] = useState('');
  const isPdf = /\.pdf$/i.test(citation.filename_text);
  const [view, setView] = useState<'pdf' | 'text'>('text');
  const sourcePage = Math.max(1, citation.page_start ?? chunk?.page_start ?? 1);

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    const controller = new AbortController();
    // Construct the endpoint from the chunk ID; source_url is untrusted display data.
    void api.chunk(citation.chunk_identifier, controller.signal).then(result => {
      if (!controller.signal.aborted) setChunk(result);
    }).catch(error => {
      if (!controller.signal.aborted && !isAbort(error)) setError(error instanceof ApiError && error.status === 404
        ? 'Полный фрагмент больше недоступен. Сохранённая цитата осталась ниже' : errorText(error));
    });
    return () => { controller.abort(); previousFocus?.focus(); };
  }, [api, citation.chunk_identifier]);

  return <dialog ref={dialog} className={`source-dialog ${isPdf ? 'pdf-source-dialog' : ''}`} aria-labelledby="source-title" onCancel={onClose}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="dialog-shell"><div className="dialog-header"><div><p className="eyebrow">ИСТОЧНИК {citation.source_label}</p>
      <h2 id="source-title">{chunk?.filename_text ?? citation.filename_text}</h2>
      <p className="muted">{pageText(chunk?.page_start ?? citation.page_start, chunk?.page_end ?? citation.page_end)}</p></div>
      <button autoFocus className="icon-button" aria-label="Закрыть фрагмент" onClick={onClose}><Icon name="close"/></button></div>
      {isPdf && <div className="source-view-tabs" role="group" aria-label="Представление источника">
        <button aria-pressed={view === 'pdf'} onClick={() => setView('pdf')}><Icon name="file" size={16}/>Оригинал PDF</button>
        <button aria-pressed={view === 'text'} onClick={() => setView('text')}><Icon name="book" size={16}/>Текстовый фрагмент</button>
      </div>}
      <div className="dialog-body source-content">
        {isPdf && <div className="source-view" hidden={view !== 'pdf'}><PdfOriginal api={api} documentId={citation.document_identifier} initialPage={sourcePage} onShowText={() => setView('text')}/></div>}
        <div className="source-view source-text-content" hidden={isPdf && view !== 'text'}>{error ? <><p className="alert" role="alert">{error}</p><Markdown text={citation.excerpt_text}/></>
          : chunk ? <Markdown text={chunk.content_text}/> : <p className="loading-line" role="status"><span className="spinner"/>Загружаю фрагмент…</p>}</div>
      </div>
    </div>
  </dialog>;
}

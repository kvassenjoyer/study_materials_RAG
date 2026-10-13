import { useEffect, useState } from 'react';
import type { ApiClient } from '../api';
import { errorText, isAbort } from '../api';
import { Icon } from './Icon';

export function PdfOriginal({ api, documentId, initialPage, onShowText }: {
  api: ApiClient; documentId: string; initialPage: number; onShowText: () => void;
}) {
  const [url, setUrl] = useState('');
  const [page, setPage] = useState(initialPage);
  const [pageCount, setPageCount] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = '';
    setUrl(''); setError(''); setPageCount(null); setPage(initialPage);
    void api.originalPdf(documentId, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(result.blob);
      setUrl(objectUrl); setPageCount(result.pageCount);
      setPage(Math.max(1, Math.min(initialPage, result.pageCount ?? initialPage)));
    }).catch(error => { if (!controller.signal.aborted && !isAbort(error)) setError(errorText(error)); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [api, documentId, initialPage, attempt]);
  if (error) return <div className="pdf-fallback"><p className="alert" role="alert">{error}</p><div className="inline-actions">
    <button className="text-button" onClick={() => setAttempt(value => value + 1)}><Icon name="retry" size={15}/>Повторить</button>
    <button className="text-button" onClick={onShowText}>Показать текстовый фрагмент</button>
  </div></div>;
  if (!url) return <p className="loading-line" role="status"><span className="spinner"/>Загружаю оригинал PDF…</p>;
  const pageUrl = `${url}#page=${page}&view=FitH`;
  return <div className="pdf-original">
    <div className="pdf-toolbar"><div className="pdf-pagination">
      <button className="icon-button" aria-label="Предыдущая страница" disabled={page <= 1} onClick={() => setPage(value => value - 1)}><Icon name="chevron" size={17} style={{ transform: 'rotate(180deg)' }}/></button>
      <span aria-live="polite">Страница {page}{pageCount !== null && ` из ${pageCount}`}</span>
      <button className="icon-button" aria-label="Следующая страница" disabled={pageCount === null || page >= pageCount} onClick={() => setPage(value => value + 1)}><Icon name="chevron" size={17}/></button>
    </div><a className="text-button" href={pageUrl} target="_blank" rel="noopener noreferrer">Открыть отдельно<Icon name="external" size={14}/></a></div>
    {navigator.pdfViewerEnabled === false ? <p className="muted">Откройте PDF отдельно или переключитесь на текстовый фрагмент</p>
      : <iframe key={page} className="pdf-frame" src={pageUrl} title={`Оригинал PDF - страница ${page}`} referrerPolicy="no-referrer"/>}
  </div>;
}

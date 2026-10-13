import type { DocumentRecord } from '../types';

export function IndexingProgress({ document }: { document: DocumentRecord }) {
  const percent = document.indexing_progress_percent ?? 0;
  const indexed = document.indexed_chunk_count ?? 0;
  const preparing = document.chunk_count === 0;
  const finishing = !preparing && indexed === document.chunk_count;
  return <div className="indexing-progress">
    <span className="document-state indexing"><span className="status-dot"/>
      {preparing ? 'Подготовка текста' : finishing ? 'Завершаю индексирование' : 'Индексируется'}<strong>{percent}%</strong>
    </span>
    <progress max={100} value={percent} aria-label={`Индексирование ${document.filename_text}`}/>
    {!preparing && <span className="indexing-details">Обработано фрагментов: {indexed.toLocaleString('ru')} из {document.chunk_count.toLocaleString('ru')}</span>}
  </div>;
}

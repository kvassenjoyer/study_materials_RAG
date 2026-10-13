import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { DocumentRecord } from '../types';
import { IndexingProgress } from './IndexingProgress';

const document: DocumentRecord = {
  document_identifier: 'book', project_identifier: 'project', filename_text: 'Ландау.pdf', media_type: 'application/pdf',
  byte_count: 8_000_000, state_name: 'indexing', chunk_count: 1000, page_count: 535, indexed_chunk_count: 320,
  indexing_progress_percent: 32, embedding_profile: 'fixture', error_code: null, error_message: null,
  created_at: '2026-10-13T00:00:00Z', updated_at: '2026-10-13T00:00:00Z',
};

describe('indexing progress', () => {
  it('shows the server percentage, processed count and accessible progress bar', () => {
    const output = renderToStaticMarkup(<IndexingProgress document={document}/>);
    expect(output).toContain('32%'); expect(output).toContain('value="32"'); expect(output).toContain('max="100"');
    expect(output).toContain('Индексирование Ландау.pdf'); expect(output).toContain('320'); expect(output).toContain('1 000');
  });

  it('distinguishes preparation from final publication', () => {
    const preparing = renderToStaticMarkup(<IndexingProgress document={{ ...document, chunk_count: 0, indexed_chunk_count: 0, indexing_progress_percent: 0 }}/>);
    expect(preparing).toContain('Подготовка текста'); expect(preparing).toContain('0%'); expect(preparing).not.toContain('Обработано фрагментов');
    const finishing = renderToStaticMarkup(<IndexingProgress document={{ ...document, indexed_chunk_count: 1000, indexing_progress_percent: 99 }}/>);
    expect(finishing).toContain('Завершаю индексирование'); expect(finishing).toContain('99%');
  });
});

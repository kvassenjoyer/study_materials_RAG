export interface AuthToken { access_token: string; expires_in: number; token_type: string }
export interface Identity { user_identifier: string; login_name: string }
export interface Page<T> { items: T[]; total_count: number; offset: number; limit: number }
export type DocumentState = 'queued' | 'indexing' | 'ready' | 'failed' | 'deleting' | 'deleted';
export interface DocumentRecord {
  project_identifier: string;
  document_identifier: string; filename_text: string; media_type: string; byte_count: number;
  state_name: DocumentState; chunk_count: number; page_count: number | null;
  indexed_chunk_count: number; indexing_progress_percent: number;
  embedding_profile: string; error_code: string | null; error_message: string | null;
  created_at: string; updated_at: string;
}
export interface ChatRecord { chat_identifier: string; project_identifier: string; title_text: string; created_at: string; updated_at: string }
export interface ProjectRecord { project_identifier: string; title_text: string; created_at: string; updated_at: string }
export interface Citation {
  source_label: string; chunk_identifier: string; document_identifier: string; filename_text: string;
  page_start: number | null; page_end: number | null; excerpt_text: string;
  similarity_score: number; source_url: string;
}
export interface MessageRecord {
  message_identifier: string; chat_identifier: string; turn_identifier: string; role_name: 'user' | 'assistant';
  content_text: string; citation_records: Citation[]; sufficient_context: boolean | null;
  turn_status: 'queued' | 'pending' | 'completed' | 'failed'; created_at: string;
  turn_error_code: string | null;
  response_duration_ms: number | null;
}
export interface ChunkRecord {
  chunk_identifier: string; document_identifier: string; ordinal_value: number; content_text: string;
  token_count: number; page_start: number | null; page_end: number | null; filename_text: string;
}
export interface QuestionRun { chatId: string; runId: string; question: string; messageId: string }
export interface AnswerJob { turn_identifier: string; chat_identifier: string; project_identifier: string; status_name: MessageRecord['turn_status'] }

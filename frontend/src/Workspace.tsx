import { useCallback, useEffect, useRef, useState } from 'react';
import type { DragEvent, FormEvent, KeyboardEvent } from 'react';
import { documentFailureText, errorText, isAbort } from './api';
import type { ApiClient } from './api';
import { startQuestion } from './ag-ui';
import { RequestScope } from './request-scope';
import type { ChatRecord, Citation, DocumentRecord, Identity, MessageRecord, QuestionRun } from './types';
import { Icon } from './components/Icon';
import { Markdown } from './components/Markdown';
import { SourceDialog, Sources } from './components/Sources';

const date = (value: string) => new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'short' }).format(new Date(value));
const fileSize = (bytes: number) => bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} МБ` : `${Math.max(1, Math.round(bytes / 1024))} КБ`;
const stateText = { queued: 'В очереди', indexing: 'Индексируется', ready: 'Готов', failed: 'Ошибка', deleting: 'Удаляется', deleted: 'Удалён' };
type ActiveRun = { request: QuestionRun; text: string; knownMessages: Set<string> };

export function Workspace({ api, token, identity, onLogout }: { api: ApiClient; token: string; identity: Identity; onLogout: () => void }) {
  const [chats, setChats] = useState<ChatRecord[]>([]);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [chatId, setChatId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageRecord[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [listLoading, setListLoading] = useState(true);
  const [libraryLoading, setLibraryLoading] = useState(true);
  const [historyError, setHistoryError] = useState('');
  const [chatError, setChatError] = useState('');
  const [documentError, setDocumentError] = useState('');
  const [question, setQuestion] = useState('');
  const [run, setRun] = useState<ActiveRun | null>(null);
  const [failedRun, setFailedRun] = useState<QuestionRun | null>(null);
  const [runError, setRunError] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [newChatOpen, setNewChatOpen] = useState(false);
  const [newChatTitle, setNewChatTitle] = useState('');
  const [creatingChat, setCreatingChat] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [documentAction, setDocumentAction] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [citation, setCitation] = useState<Citation | null>(null);
  const scope = useRef(new RequestScope());
  const selectedChat = useRef<string | null>(null);
  const requests = useRef(new Set<AbortController>());
  const historyRequest = useRef<AbortController | null>(null);
  const documentsRequest = useRef<AbortController | null>(null);
  const documentSequence = useRef(0);
  const chatSequence = useRef(0);
  const agent = useRef<ReturnType<typeof startQuestion> | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const conversation = useRef<HTMLDivElement>(null);

  const tracked = useCallback(async <T,>(operation: (signal: AbortSignal) => Promise<T>): Promise<T> => {
    const controller = new AbortController(); requests.current.add(controller);
    try { return await operation(controller.signal); }
    finally { requests.current.delete(controller); }
  }, []);

  const loadHistory = useCallback(async (id: string, showLoading = true) => {
    historyRequest.current?.abort();
    const controller = new AbortController(); historyRequest.current = controller;
    const current = scope.current.selection();
    if (showLoading) setHistoryLoading(true);
    setHistoryError('');
    try {
      const result = await api.messages(id, controller.signal);
      if (current() && !controller.signal.aborted) setMessages(result);
    } catch (error) {
      if (current() && !controller.signal.aborted && !isAbort(error)) { setHistoryError(errorText(error)); throw error; }
    } finally { if (current() && !controller.signal.aborted) setHistoryLoading(false); }
  }, [api]);

  const selectChat = useCallback((id: string) => {
    scope.current.select(id); selectedChat.current = id;
    agent.current?.abort(); agent.current = null;
    setChatId(id); setMessages([]); setRun(null); setFailedRun(null); setRunError(''); setQuestion('');
    setCitation(null); setSidebarOpen(false);
    void loadHistory(id).catch(() => {});
  }, [loadHistory]);

  const loadChats = useCallback(async (initial = false) => {
    const current = scope.current.session(); const sequence = ++chatSequence.current;
    try {
      const result = await tracked(signal => api.chats(signal));
      if (!current() || sequence !== chatSequence.current) return;
      const sorted = result.sort((left, right) => right.updated_at.localeCompare(left.updated_at));
      setChats(sorted); setChatError('');
      if (initial && !selectedChat.current && sorted[0]) selectChat(sorted[0].chat_identifier);
    } catch (error) { if (current() && sequence === chatSequence.current && !isAbort(error)) setChatError(errorText(error)); }
    finally { if (current() && sequence === chatSequence.current) setListLoading(false); }
  }, [api, tracked, selectChat]);

  const loadDocuments = useCallback(async () => {
    documentsRequest.current?.abort();
    const controller = new AbortController(); documentsRequest.current = controller;
    const current = scope.current.session(); const sequence = ++documentSequence.current;
    try {
      const result = await api.documents(controller.signal);
      if (current() && !controller.signal.aborted && sequence === documentSequence.current) {
        setDocuments(result.filter(item => item.state_name !== 'deleted').sort((left, right) => right.created_at.localeCompare(left.created_at)));
        setDocumentError('');
      }
    } catch (error) { if (current() && !controller.signal.aborted && sequence === documentSequence.current && !isAbort(error)) setDocumentError(errorText(error)); }
    finally { if (current() && !controller.signal.aborted && sequence === documentSequence.current) setLibraryLoading(false); }
  }, [api]);

  useEffect(() => {
    void loadChats(true); void loadDocuments();
    const currentScope = scope.current;
    const activeRequests = requests.current;
    return () => {
      currentScope.dispose(); agent.current?.abort(); historyRequest.current?.abort(); documentsRequest.current?.abort();
      for (const request of activeRequests) request.abort();
    };
  }, [loadChats, loadDocuments]);

  const hasPendingDocuments = documents.some(item => ['queued', 'indexing', 'deleting'].includes(item.state_name));
  useEffect(() => {
    if (!hasPendingDocuments) return;
    let stopped = false;
    let timer: number;
    const poll = async () => {
      await loadDocuments();
      if (!stopped) timer = window.setTimeout(() => void poll(), 2500);
    };
    timer = window.setTimeout(() => void poll(), 2500);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [hasPendingDocuments, loadDocuments]);

  useEffect(() => {
    const area = conversation.current;
    if (area && area.scrollHeight - area.scrollTop - area.clientHeight < 260) end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, run?.text]);

  async function createChat(event?: FormEvent) {
    event?.preventDefault(); if (creatingChat) return;
    const current = scope.current.session(); const selection = scope.current.selection();
    setCreatingChat(true); setChatError('');
    try {
      const result = await tracked(signal => api.createChat(newChatTitle.trim() || 'Новый чат', signal));
      if (!current()) return;
      setChats(previous => [result, ...previous]); setNewChatOpen(false); setNewChatTitle('');
      if (selection()) selectChat(result.chat_identifier);
    } catch (error) { if (current() && !isAbort(error)) setChatError(errorText(error)); }
    finally { if (current()) setCreatingChat(false); }
  }

  async function uploadFiles(files: File[]) {
    if (uploading || !files.length) return;
    const current = scope.current.session(); setUploading(true); setDocumentError('');
    try {
      for (const file of files) {
        if (!current()) break;
        if (!/\.(txt|md|pdf)$/i.test(file.name)) { setDocumentError('Поддерживаются файлы TXT, MD и PDF.'); continue; }
        if (file.size > 100 * 1024 * 1024) { setDocumentError('Файл превышает 100 МБ. Выберите файл меньшего размера.'); continue; }
        const result = await tracked(signal => api.upload(file, signal));
        if (current()) setDocuments(previous => [result, ...previous.filter(item => item.document_identifier !== result.document_identifier)]);
      }
    } catch (error) { if (current() && !isAbort(error)) setDocumentError(errorText(error)); }
    finally { if (current()) { setUploading(false); if (fileInput.current) fileInput.current.value = ''; } }
  }

  async function mutateDocument(document: DocumentRecord, action: 'retry' | 'delete') {
    if (documentAction) return;
    const current = scope.current.session(); setDocumentAction(document.document_identifier); setDeleteConfirm(null); setDocumentError('');
    try {
      const result = await tracked(signal => action === 'retry'
        ? api.retryDocument(document.document_identifier, signal) : api.deleteDocument(document.document_identifier, signal));
      if (current()) setDocuments(previous => previous.map(item => item.document_identifier === result.document_identifier ? result : item));
    } catch (error) { if (current() && !isAbort(error)) setDocumentError(errorText(error)); }
    finally { if (current()) setDocumentAction(null); }
  }

  async function sendQuestion(retry?: QuestionRun) {
    const id = selectedChat.current; const text = retry?.question ?? question.trim();
    if (!id || run || !text || text.length > 4000 || historyLoading) return;
    const request: QuestionRun = retry ?? { chatId: id, runId: crypto.randomUUID(), messageId: crypto.randomUUID(), question: text };
    const current = scope.current.selection();
    setRun({ request, text: '', knownMessages: new Set(messages.map(message => message.message_identifier)) }); setQuestion(''); setRunError(''); setFailedRun(null);
    const activeAgent = startQuestion(api, token, request, {
      isCurrent: current,
      onText: text => setRun(previous => previous?.request.runId === request.runId ? { ...previous, text } : previous),
      onFinished: async () => { await loadHistory(id, false); if (current()) void loadChats(); },
    });
    agent.current = activeAgent;
    try { await activeAgent.completion; }
    catch (error) {
      if (!current()) return;
      setRunError(errorText(error)); setFailedRun(request); setQuestion(request.question);
      // A lost connection can follow a successful commit; REST is authoritative.
      try { await loadHistory(id, false); } catch { /* The history error has its own visible retry action. */ }
    } finally {
      if (current()) { setRun(null); agent.current = null; textarea.current?.focus(); }
    }
  }

  function onComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault(); void sendQuestion(failedRun && question.trim() === failedRun.question ? failedRun : undefined);
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault(); setDragging(false); void uploadFiles(Array.from(event.dataTransfer.files));
  }

  const readyCount = documents.filter(item => item.state_name === 'ready').length;
  const activeChat = chats.find(item => item.chat_identifier === chatId);
  const persistedRun = run && messages.some(item => item.role_name === 'assistant' && !run.knownMessages.has(item.message_identifier));
  const optimisticQuestion = run && !messages.some(item => item.role_name === 'user' && !run.knownMessages.has(item.message_identifier));

  return <div className="workspace">
    {sidebarOpen && <button className="sidebar-backdrop" aria-label="Закрыть меню" onClick={() => setSidebarOpen(false)}/>}
    <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`} aria-label="Чаты и материалы"><div className="sidebar-top">
      <a href="/" className="brand"><span className="brand-icon"><Icon name="book" size={22}/></span>mini<span>rag</span></a>
      <button className="icon-button mobile-only" aria-label="Закрыть меню" onClick={() => setSidebarOpen(false)}><Icon name="close"/></button>
    </div><div className="sidebar-content">
      <button className="new-chat-button" onClick={() => setNewChatOpen(value => !value)}><Icon name="plus"/>Новый чат</button>
      {newChatOpen && <form className="new-chat-form" onSubmit={event => void createChat(event)}><label htmlFor="chat-title">Название чата</label>
        <input id="chat-title" autoFocus placeholder="Новый чат" maxLength={200} value={newChatTitle} onChange={event => setNewChatTitle(event.target.value)} disabled={creatingChat}/>
        <div className="inline-actions"><button type="submit" className="small-primary" disabled={creatingChat}>{creatingChat ? 'Создаю…' : 'Создать'}</button><button type="button" className="text-button" onClick={() => setNewChatOpen(false)}>Отмена</button></div></form>}
      <section className="chat-section"><div className="section-heading"><h2>Чаты</h2><span>{chats.length}</span></div>
        {listLoading ? <p className="sidebar-empty" role="status">Загружаю чаты…</p> : chats.length ? <nav className="chat-list" aria-label="История чатов">{chats.map(chat => <button key={chat.chat_identifier}
          className={`chat-item ${chat.chat_identifier === chatId ? 'selected' : ''}`} aria-current={chat.chat_identifier === chatId ? 'page' : undefined}
          onClick={() => selectChat(chat.chat_identifier)}><Icon name="chat" size={18}/><span><span className="chat-name">{chat.title_text}</span><span className="chat-date">{date(chat.updated_at)}</span></span></button>)}</nav>
          : <p className="sidebar-empty">Первый вопрос начинается<br/>с нового чата.</p>}
        {chatError && <div className="sidebar-alert"><p role="alert">{chatError}</p><button className="text-button" onClick={() => void loadChats()}>Обновить</button></div>}
      </section>
      <section className="library-section"><div className="section-heading"><h2>Материалы</h2><span>{documents.length}</span><button className="icon-button" aria-label="Обновить материалы" onClick={() => void loadDocuments()}><Icon name="retry" size={16}/></button></div>
        <div className={`upload-zone ${dragging ? 'dragging' : ''} ${uploading ? 'busy' : ''}`} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
          <input ref={fileInput} className="visually-hidden" type="file" id="material-file" aria-label="Загрузить учебные материалы" accept=".txt,.md,.pdf,text/plain,text/markdown,application/pdf" multiple disabled={uploading}
            onChange={event => void uploadFiles(Array.from(event.target.files ?? []))}/>
          <button disabled={uploading} onClick={() => fileInput.current?.click()}><Icon name="upload" size={22}/><strong>{uploading ? 'Загружаю…' : 'Добавить материалы'}</strong><span>TXT, MD, PDF · до 100 МБ</span></button>
          <p>или перетащите файлы сюда</p>
        </div>
        {documentError && <p className="sidebar-alert" role="alert">{documentError}</p>}
        {libraryLoading ? <p className="sidebar-empty" role="status">Загружаю материалы…</p> : !documents.length ? <p className="sidebar-empty">Конспекты, лекции, учебники —<br/>всё, к чему хочется вернуться.</p> : <ul className="document-list">{documents.map(document => <li className="document-item" key={document.document_identifier}>
          <span className="file-icon"><Icon name="file" size={19}/></span><div className="document-info"><span className="document-name" title={document.filename_text}>{document.filename_text}</span>
            <span className="document-details">{fileSize(document.byte_count)}{document.page_count ? ` · ${document.page_count} стр.` : ''}</span>
            <span className={`document-state ${document.state_name}`}>{['queued', 'indexing', 'deleting'].includes(document.state_name) && <span className="status-dot"/>}{stateText[document.state_name]}</span>
            {document.state_name === 'failed' && <><p className="document-failure">{documentFailureText(document.error_code, document.error_message)}</p><button className="text-button" disabled={documentAction !== null} onClick={() => void mutateDocument(document, 'retry')}><Icon name="retry" size={13}/>Повторить</button></>}
            {deleteConfirm === document.document_identifier && <div className="delete-confirm"><span>Удалить материал?</span><button className="danger-text" onClick={() => void mutateDocument(document, 'delete')}>Удалить</button><button className="text-button" onClick={() => setDeleteConfirm(null)}>Отмена</button></div>}
          </div><button className="icon-button document-delete" aria-label={`Удалить ${document.filename_text}`} disabled={documentAction !== null || document.state_name === 'deleting'} onClick={() => setDeleteConfirm(document.document_identifier)}><Icon name="trash" size={15}/></button>
        </li>)}</ul>}
      </section>
    </div><div className="sidebar-bottom"><span className="avatar">{identity.email_address.slice(0, 1).toUpperCase()}</span><span className="account-email" title={identity.email_address}>{identity.email_address}</span><button className="icon-button" aria-label="Выйти из аккаунта" onClick={onLogout}><Icon name="logout" size={19}/></button></div></aside>

    <main className="main-panel"><header className="topbar"><button className="icon-button mobile-only" aria-label="Открыть меню" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(true)}><Icon name="menu"/></button>
      <div className="breadcrumb"><span>Библиотека</span><Icon name="chevron" size={13}/><strong>{activeChat?.title_text ?? 'Рабочее пространство'}</strong></div>
      <a className="api-link" href="/docs" target="_blank" rel="noopener noreferrer">API<Icon name="external" size={13}/></a>
    </header>
    <div className="conversation" ref={conversation}>
      {historyLoading ? <div className="history-loading" role="status"><span className="spinner"/>Загружаю переписку…</div> : !messages.length && !run ? <section className="welcome"><span className="welcome-icon"><Icon name="book" size={34}/></span>
        <p className="eyebrow">МАЛЕНЬКИЕ ВОПРОСЫ. БОЛЬШИЕ ОТКРЫТИЯ.</p><h1>Разберёмся вместе.</h1><p className="welcome-description">Загрузите учебные материалы и задайте вопрос.<br/>Ответ поможет связать идеи, а источники — проверить детали.</p>
        <div className="welcome-steps"><div><span>01</span><strong>Добавьте материалы</strong><p>Конспект, статью или главу учебника</p></div><div><span>02</span><strong>Спросите о важном</strong><p>Понятным языком, как на занятии</p></div><div><span>03</span><strong>Откройте источник</strong><p>Вернитесь к точному фрагменту</p></div></div>
        {!chatId && <button className="primary-button" onClick={() => { setSidebarOpen(true); setNewChatOpen(true); }}><Icon name="plus" size={18}/>Создать первый чат</button>}
        {chatId && <div className="suggestion-list">{['Объясни основную идею материала', 'Какие понятия нужно запомнить?', 'Приведи пример из моего конспекта'].map(text => <button key={text} onClick={() => { setQuestion(text); textarea.current?.focus(); }}>{text}<Icon name="chevron" size={14}/></button>)}</div>}
      </section> : <div className="messages">{messages.map(message => <article className={`message ${message.role_name}`} key={message.message_identifier}>
        <div className="message-heading"><span className={`message-avatar ${message.role_name}`}>{message.role_name === 'assistant' ? <Icon name="spark" size={17}/> : identity.email_address.slice(0, 1).toUpperCase()}</span><strong>{message.role_name === 'assistant' ? 'Mini-RAG' : 'Вы'}</strong><span className="message-time">{new Intl.DateTimeFormat('ru', { hour: '2-digit', minute: '2-digit' }).format(new Date(message.created_at))}</span></div>
        <div className="message-body"><Markdown text={message.content_text}/>{message.role_name === 'user' && message.turn_status !== 'completed' && <span className={`turn-state ${message.turn_status}`}>{message.turn_status === 'pending' ? 'Ответ ещё готовится' : 'Ответ не был получен'}</span>}
          {message.role_name === 'assistant' && <Sources citations={message.citation_records} onOpen={setCitation}/>}</div>
      </article>)}
      {optimisticQuestion && run && <article className="message user"><div className="message-heading"><span className="message-avatar user">{identity.email_address.slice(0, 1).toUpperCase()}</span><strong>Вы</strong></div><div className="message-body"><Markdown text={run.request.question}/></div></article>}
      {run && !persistedRun && <article className="message assistant pending-response"><div className="message-heading"><span className="message-avatar assistant"><Icon name="spark" size={17}/></span><strong>Mini-RAG</strong></div><div className="message-body">{run.text ? <Markdown text={run.text}/> : <p className="loading-line" role="status"><span className="spinner"/>Ищу ответ в материалах…</p>}</div></article>}
      </div>}
      {historyError && <div className="history-error"><p className="alert" role="alert">{historyError}</p><button className="text-button" onClick={() => chatId && void loadHistory(chatId).catch(() => {})}>Обновить историю</button></div>}
      <div ref={end}/>
    </div>
    <footer className="composer-area">{runError && <div className="run-error"><p role="alert">{runError}</p>{failedRun && <button className="text-button" disabled={!!run || historyLoading} onClick={() => void sendQuestion(failedRun)}><Icon name="retry" size={14}/>Повторить запрос</button>}</div>}
      {!readyCount && <p className="material-hint"><Icon name="book" size={15}/>{documents.length ? 'Материалы пока не готовы. Ответы появятся после индексации.' : 'Добавьте материалы, чтобы получать ответы с источниками.'}</p>}
      <form className="composer" onSubmit={event => { event.preventDefault(); void sendQuestion(failedRun && question.trim() === failedRun.question ? failedRun : undefined); }}>
        <label className="visually-hidden" htmlFor="question">Вопрос по учебным материалам</label><textarea ref={textarea} id="question" placeholder={chatId ? 'Что хотите понять в ваших материалах?' : 'Создайте чат, чтобы задать вопрос'} value={question} maxLength={4000} rows={2} disabled={!chatId || !!run || historyLoading}
          onChange={event => { setQuestion(event.target.value); setFailedRun(null); setRunError(''); }} onKeyDown={onComposerKey}/>
        <button className="send-button" type="submit" disabled={!chatId || !question.trim() || !!run || historyLoading} aria-label="Отправить вопрос">{run ? <span className="spinner"/> : <Icon name="arrow" size={21}/>}</button>
      </form><div className="composer-foot"><span>Enter — отправить · Shift + Enter — новая строка</span><span>{question.length ? `${question.length} / 4000` : 'Проверяйте важные детали по источникам'}</span></div>
    </footer></main>
    {citation && <SourceDialog key={citation.chunk_identifier} api={api} citation={citation} onClose={() => setCitation(null)}/>}
  </div>;
}

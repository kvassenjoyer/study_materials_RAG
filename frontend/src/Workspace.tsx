import { startQuestion } from './ag-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { DragEvent, KeyboardEvent } from 'react';
import { documentFailureText, errorText, isAbort } from './api';
import type { ApiClient } from './api';
import { RequestScope } from './request-scope';
import type { ChatRecord, Citation, DocumentRecord, Identity, MessageRecord, QuestionRun, ProjectRecord } from './types';
import { Icon } from './components/Icon';
import { Markdown } from './components/Markdown';
import { SourceDialog, Sources } from './components/Sources';
import { ProjectSwitcher } from './components/ProjectSwitcher';
import { ManagementDialog } from './components/ManagementDialog';
import { IndexingProgress } from './components/IndexingProgress';

const date = (value: string) => new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'short' }).format(new Date(value));
const fileSize = (bytes: number) => bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} МБ` : `${Math.max(1, Math.round(bytes / 1024))} КБ`;
const stateText = { queued: 'В очереди', indexing: 'Индексируется', ready: 'Готов', failed: 'Ошибка', deleting: 'Удаляется', deleted: 'Удалён' };
type ActiveRun = { request: QuestionRun; text: string; knownMessages: Set<string> };

export function Workspace({ api, token, identity, onLogout }: { api: ApiClient; token: string; identity: Identity; onLogout: () => void }) {
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void api.projects(controller.signal).then(result => {
      if (controller.signal.aborted) return;
      setProjects(result); setError(''); setLoaded(true);
      setProjectId(current => result.some(project => project.project_identifier === current) ? current : result[0]?.project_identifier ?? null);
    }).catch(error => { if (!controller.signal.aborted && !isAbort(error)) setError(errorText(error)); });
    return () => controller.abort();
  }, [api, reload]);
  const project = projects.find(item => item.project_identifier === projectId);
  const projectActions = {
    onSelectProject: setProjectId,
    onProjectCreated: (created: ProjectRecord) => { setProjects(previous => [...previous, created]); setProjectId(created.project_identifier); },
    onProjectUpdated: (updated: ProjectRecord) => setProjects(previous => previous.map(item => item.project_identifier === updated.project_identifier ? updated : item)),
    onProjectDeleted: (id: string) => {
      const remaining = projects.filter(item => item.project_identifier !== id); setProjects(remaining);
      setProjectId(current => current === id ? remaining[0]?.project_identifier ?? null : current);
    },
  };
  if (!project) return <main className="project-loading"><div className="project-loading-heading"><span className="brand"><Icon name="book"/>Мини-RAG</span></div>
    {error ? <><p className="alert" role="alert">{error}</p><button className="primary-button" onClick={() => setReload(value => value + 1)}>Повторить</button></>
      : loaded ? <><h1>Начнём с проекта</h1><p>Создайте пространство для новой темы. В нём будут ваши чаты и материалы</p>
        <ProjectSwitcher api={api} projects={projects} onSelect={setProjectId} onCreated={projectActions.onProjectCreated}
          onUpdated={projectActions.onProjectUpdated} onDeleted={projectActions.onProjectDeleted}/></>
        : <p className="loading-line" role="status"><span className="spinner"/>Загружаю проекты…</p>}
    <button className="text-button" onClick={onLogout}>Выйти из аккаунта</button>
  </main>;
  // Remounting closes local observers; accepted answers remain owned by the server.
  return <ProjectWorkspace key={project.project_identifier} api={api} token={token} identity={identity} onLogout={onLogout}
    project={project} projects={projects} {...projectActions}/>
}

function ProjectWorkspace({ api, token, identity, onLogout, project, projects, onSelectProject, onProjectCreated, onProjectUpdated, onProjectDeleted }: {
  api: ApiClient; token: string; identity: Identity; onLogout: () => void;
  project: ProjectRecord; projects: ProjectRecord[]; onSelectProject: (id: string) => void; onProjectCreated: (project: ProjectRecord) => void;
  onProjectUpdated: (project: ProjectRecord) => void; onProjectDeleted: (id: string) => void;
}) {
  const projectId = project.project_identifier;
  const [chats, setChats] = useState<ChatRecord[]>([]);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [chatId, setChatId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageRecord[]>([]);
  const messagesRef = useRef<MessageRecord[]>([]);
  useEffect(() => { messagesRef.current = messages; }, [messages]);
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
  const [creatingChat, setCreatingChat] = useState(false);
  const [chatMenu, setChatMenu] = useState<string | null>(null);
  const [chatAction, setChatAction] = useState<{ kind: 'rename' | 'delete'; chat: ChatRecord } | null>(null);
  const startingChat = useRef(false);
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
    setChatId(id); setMessages([]); setRun(null); setFailedRun(null); setRunError(''); setQuestion('');
    setCitation(null); setSidebarOpen(false);
    void loadHistory(id).catch(() => {});
  }, [loadHistory]);

  const loadChats = useCallback(async (initial = false) => {
    const current = scope.current.session(); const sequence = ++chatSequence.current;
    const selection = scope.current.selection();
    try {
      const result = await tracked(signal => api.chats(projectId, signal));
      if (!current() || sequence !== chatSequence.current) return;
      const sorted = result.sort((left, right) => right.updated_at.localeCompare(left.updated_at));
      setChats(sorted); setChatError('');
      if (initial && selection() && !selectedChat.current && sorted[0]) selectChat(sorted[0].chat_identifier);
    } catch (error) { if (current() && sequence === chatSequence.current && !isAbort(error)) setChatError(errorText(error)); }
    finally { if (current() && sequence === chatSequence.current) setListLoading(false); }
  }, [api, tracked, selectChat, projectId]);

  const loadDocuments = useCallback(async () => {
    documentsRequest.current?.abort();
    const controller = new AbortController(); documentsRequest.current = controller;
    const current = scope.current.session(); const sequence = ++documentSequence.current;
    try {
      const result = await api.documents(projectId, controller.signal);
      if (current() && !controller.signal.aborted && sequence === documentSequence.current) {
        setDocuments(result.filter(item => item.state_name !== 'deleted').sort((left, right) => right.created_at.localeCompare(left.created_at)));
        setDocumentError('');
      }
    } catch (error) { if (current() && !controller.signal.aborted && sequence === documentSequence.current && !isAbort(error)) setDocumentError(errorText(error)); }
    finally { if (current() && !controller.signal.aborted && sequence === documentSequence.current) setLibraryLoading(false); }
  }, [api, projectId]);

  useEffect(() => {
    void loadChats(true); void loadDocuments();
    const currentScope = scope.current;
    const activeRequests = requests.current;
    return () => {
      currentScope.dispose(); historyRequest.current?.abort(); documentsRequest.current?.abort();
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

  function newChat() {
    scope.current.select(null); selectedChat.current = null;
    historyRequest.current?.abort();
    setChatId(null); setMessages([]); setRun(null); setFailedRun(null); setRunError(''); setQuestion('');
    setHistoryLoading(false); setHistoryError(''); setCitation(null); setSidebarOpen(false); setChatMenu(null);
    window.requestAnimationFrame(() => textarea.current?.focus());
  }

  async function uploadFiles(files: File[]) {
    if (uploading || !files.length) return;
    const current = scope.current.session(); setUploading(true); setDocumentError('');
    try {
      for (const file of files) {
        if (!current()) break;
        if (!/\.(txt|md|pdf)$/i.test(file.name)) { setDocumentError('Поддерживаются файлы TXT, MD и PDF'); continue; }
        if (file.size > 100 * 1024 * 1024) { setDocumentError('Файл превышает 100 МБ. Выберите файл меньшего размера'); continue; }
        const result = await tracked(signal => api.upload(file, projectId, signal));
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
    let id = selectedChat.current; const text = retry?.question ?? question.trim();
    if (startingChat.current || run || !text || text.length > 4000 || historyLoading) return;
    if (!id) {
      startingChat.current = true; setCreatingChat(true); setRunError('');
      const selection = scope.current.selection(); const session = scope.current.session();
      try {
        const created = await tracked(signal => api.createChat(projectId, signal));
        if (!session()) return;
        setChats(previous => [created, ...previous]);
        if (!selection()) return;
        id = created.chat_identifier; scope.current.select(id); selectedChat.current = id; setChatId(id);
      } catch (error) { if (selection() && !isAbort(error)) setRunError(errorText(error)); return; }
      finally { startingChat.current = false; if (session()) setCreatingChat(false); }
    }
    const request: QuestionRun = retry ?? { chatId: id, runId: crypto.randomUUID(), messageId: crypto.randomUUID(), question: text };
    const current = scope.current.selection();
    setRun({ request, text: '', knownMessages: new Set(messages.map(message => message.message_identifier)) }); setQuestion(''); setRunError(''); setFailedRun(null);
    try {
      await startQuestion(api, token, request, {
        isCurrent: current,
        onText: text => setRun(previous => previous?.request.runId === request.runId ? { ...previous, text } : previous),
        onFinished: async () => { await loadHistory(id, false); if (current()) void loadChats(); },
      }).completion;
    }
    catch (error) {
      if (!current()) return;
      setRunError(errorText(error)); setFailedRun(request); setQuestion(request.question);
      // A lost connection can follow a successful commit; REST is authoritative.
      try { await loadHistory(id, false); } catch { /* The history error has its own visible retry action. */ }
      if (current()) void loadChats();
    } finally {
      if (current()) { setRun(null); textarea.current?.focus(); }
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

  const activeChat = chats.find(item => item.chat_identifier === chatId);
  const persistedRun = run && messages.some(item => item.role_name === 'assistant' && !run.knownMessages.has(item.message_identifier));
  const optimisticQuestion = run && !messages.some(item => item.role_name === 'user' && !run.knownMessages.has(item.message_identifier));

  return <div className="workspace">
    {sidebarOpen && <button className="sidebar-backdrop" aria-label="Закрыть меню" onClick={() => setSidebarOpen(false)}/>}
    <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`} aria-label="Чаты и материалы"><div className="sidebar-top">
      <span className="brand"><Icon name="book"/>Мини-RAG</span>
      <button className="icon-button mobile-only" aria-label="Закрыть меню" onClick={() => setSidebarOpen(false)}><Icon name="close"/></button>
    </div><div className="sidebar-content">
      <section className="project-section" aria-label="Проекты">
        <ProjectSwitcher api={api} project={project} projects={projects} onSelect={onSelectProject} onCreated={onProjectCreated}
          onUpdated={onProjectUpdated} onDeleted={onProjectDeleted}/>
      </section>
      <button className="new-chat-button" onClick={newChat} disabled={creatingChat}><Icon name="plus"/>Новый чат</button>
      <section className="chat-section"><div className="section-heading"><h2>Чаты</h2><span>{chats.length}</span></div>
        {listLoading ? <p className="sidebar-empty" role="status">Загружаю чаты…</p> : chats.length ? <nav className="chat-list" aria-label="История чатов">{chats.map(chat => <div className="chat-row" key={chat.chat_identifier}><button
          className={`chat-item ${chat.chat_identifier === chatId ? 'selected' : ''}`} aria-current={chat.chat_identifier === chatId ? 'page' : undefined}
          onClick={() => { setChatMenu(null); selectChat(chat.chat_identifier); }}><Icon name="chat" size={18}/><span><span className="chat-name">{chat.title_text}</span><span className="chat-date">{date(chat.updated_at)}</span></span></button>
          <button className="icon-button chat-more" aria-label={`Действия чата ${chat.title_text}`} aria-expanded={chatMenu === chat.chat_identifier} onClick={() => setChatMenu(current => current === chat.chat_identifier ? null : chat.chat_identifier)}><Icon name="more" size={17}/></button>
          {chatMenu === chat.chat_identifier && <div className="chat-actions"><button onClick={() => { setChatMenu(null); setChatAction({ kind: 'rename', chat }); }}><Icon name="edit" size={14}/>Переименовать</button>
            <button className="danger-text" onClick={() => { setChatMenu(null); setChatAction({ kind: 'delete', chat }); }}><Icon name="trash" size={14}/>Удалить</button></div>}
        </div>)}</nav>
          : <p className="sidebar-empty">В этом проекте пока нет сохранённых чатов</p>}
        {chatError && <div className="sidebar-alert"><p role="alert">{chatError}</p><button className="text-button" onClick={() => void loadChats()}>Обновить</button></div>}
      </section>
      <section className="library-section"><div className="section-heading"><h2>Материалы</h2><span>{documents.length}</span></div>
        <div className={`upload-zone ${dragging ? 'dragging' : ''} ${uploading ? 'busy' : ''}`} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
          <input ref={fileInput} className="visually-hidden" type="file" id="material-file" aria-label="Загрузить учебные материалы" accept=".txt,.md,.pdf,text/plain,text/markdown,application/pdf" multiple disabled={uploading}
            onChange={event => void uploadFiles(Array.from(event.target.files ?? []))}/>
          <button disabled={uploading} onClick={() => fileInput.current?.click()}><Icon name="upload" size={22}/><strong>{uploading ? 'Загружаю…' : 'Добавить материалы'}</strong><span>TXT, MD, PDF · до 100 МБ</span></button>
          <p>или перетащите файлы сюда</p>
        </div>
        {documentError && <p className="sidebar-alert" role="alert">{documentError}</p>}
        {libraryLoading ? <p className="sidebar-empty" role="status">Загружаю материалы…</p> : !documents.length ? null : <ul className="document-list">{documents.map(document => <li className="document-item" key={document.document_identifier}>
          <span className="file-icon"><Icon name="file" size={19}/></span><div className="document-info"><span className="document-name" title={document.filename_text}>{document.filename_text}</span>
            <span className="document-details">{fileSize(document.byte_count)}{document.page_count ? ` · ${document.page_count} стр` : ''}</span>
            {document.state_name === 'indexing' ? <IndexingProgress document={document}/> : <span className={`document-state ${document.state_name}`}>{['queued', 'deleting'].includes(document.state_name) && <span className="status-dot"/>}{stateText[document.state_name]}</span>}
            {document.state_name === 'failed' && <><p className="document-failure">{documentFailureText(document.error_code, document.error_message)}</p><button className="text-button" disabled={documentAction !== null} onClick={() => void mutateDocument(document, 'retry')}><Icon name="retry" size={13}/>Повторить</button></>}
            {deleteConfirm === document.document_identifier && <div className="delete-confirm"><span>Удалить материал?</span><button className="danger-text" onClick={() => void mutateDocument(document, 'delete')}>Удалить</button><button className="text-button" onClick={() => setDeleteConfirm(null)}>Отмена</button></div>}
          </div><button className="icon-button document-delete" aria-label={`Удалить ${document.filename_text}`} disabled={documentAction !== null || document.state_name === 'deleting'} onClick={() => setDeleteConfirm(document.document_identifier)}><Icon name="trash" size={15}/></button>
        </li>)}</ul>}
      </section>
    </div><div className="sidebar-bottom"><span className="avatar">{identity.login_name.slice(0, 1).toUpperCase()}</span><span className="account-login" title={identity.login_name}>{identity.login_name}</span><button className="icon-button" aria-label="Выйти из аккаунта" onClick={onLogout}><Icon name="logout" size={19}/></button></div></aside>

    <main className="main-panel"><header className="topbar"><button className="icon-button mobile-only" aria-label="Открыть меню" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(true)}><Icon name="menu"/></button>
      <div className="breadcrumb"><span>{project.title_text}</span><Icon name="chevron" size={13}/><strong>{activeChat?.title_text ?? 'Рабочее пространство'}</strong></div>
      
      <a className="api-link" href="/docs" target="_blank" rel="noopener noreferrer">API<Icon name="external" size={13}/></a>
    </header>
    <div className="conversation" ref={conversation}>
      {historyLoading ? <div className="history-loading" role="status"><span className="spinner"/>Загружаю переписку…</div> : !messages.length && !run ? <section className="welcome"><span className="welcome-icon"><Icon name="book" size={34}/></span>
        <p className="eyebrow">МАЛЕНЬКИЕ ВОПРОСЫ. БОЛЬШИЕ ОТКРЫТИЯ</p><h1>Разберёмся вместе</h1><p className="welcome-description">Загрузите учебные материалы и задайте вопрос.<br/>Ответ поможет связать идеи, а источники - проверить детали</p>
        <div className="welcome-steps"><div><span>01</span><strong>Добавьте материалы</strong><p>Конспект, статью или главу учебника</p></div><div><span>02</span><strong>Спросите о важном</strong><p>Понятным языком, как на занятии</p></div><div><span>03</span><strong>Откройте источник</strong><p>Вернитесь к точному фрагменту</p></div></div>
        {!chatId && <p className="new-chat-hint">Задайте первый вопрос ниже - чат создастся автоматически</p>}
        {<div className="suggestion-list">{['Объясни основную идею материала', 'Какие понятия нужно запомнить?', 'Приведи пример из моего конспекта'].map(text => <button key={text} onClick={() => { setQuestion(text); textarea.current?.focus(); }}>{text}<Icon name="chevron" size={14}/></button>)}</div>}
      </section> : <div className="messages">{messages.map(message => <article className={`message ${message.role_name}`} key={message.message_identifier}>
        <div className="message-heading"><span className={`message-avatar ${message.role_name}`}>{message.role_name === 'assistant' ? <Icon name="spark" size={17}/> : identity.login_name.slice(0, 1).toUpperCase()}</span><strong>{message.role_name === 'assistant' ? 'Помощник' : 'Вы'}</strong><span className="message-time">{new Intl.DateTimeFormat('ru', { hour: '2-digit', minute: '2-digit' }).format(new Date(message.created_at))}</span></div>
        <div className="message-body"><Markdown text={message.content_text}/>{message.role_name === 'user' && message.turn_status === 'failed' && <span className="turn-state failed">Ответ не был получен. Попробуйте повторить запрос</span>}
          {message.role_name === 'assistant' && <Sources citations={message.citation_records} onOpen={setCitation}/>}</div>
      </article>)}
      {optimisticQuestion && run && <article className="message user"><div className="message-heading"><span className="message-avatar user">{identity.login_name.slice(0, 1).toUpperCase()}</span><strong>Вы</strong></div><div className="message-body"><Markdown text={run.request.question}/></div></article>}
      {(run && !persistedRun) && <article className="message assistant pending-response"><div className="message-heading"><span className="message-avatar assistant"><Icon name="spark" size={17}/></span><strong>{'Помощник'}</strong></div><div className="message-body"><p className="loading-line" role="status"><span className="spinner"/>{run?.text || 'Готовлю ответ…'}</p></div></article>}
      </div>}
      {historyError && <div className="history-error"><p className="alert" role="alert">{historyError}</p><button className="text-button" onClick={() => chatId && void loadHistory(chatId).catch(() => {})}>Обновить историю</button></div>}
      <div ref={end}/>
    </div>
    <footer className="composer-area">{runError && <div className="run-error"><p role="alert">{runError}</p>{failedRun && <button className="text-button" disabled={!!run || historyLoading} onClick={() => void sendQuestion(failedRun)}><Icon name="retry" size={14}/>Повторить запрос</button>}</div>}
      <form className="composer" onSubmit={event => { event.preventDefault(); void sendQuestion(failedRun && question.trim() === failedRun.question ? failedRun : undefined); }}>
        <label className="visually-hidden" htmlFor="question">Ваш вопрос</label><textarea ref={textarea} id="question" placeholder={chatId ? 'Что хотите узнать?' : 'Задайте вопрос, чтобы начать новый чат…'} value={question} maxLength={4000} rows={2} disabled={creatingChat || !!run || historyLoading || listLoading}
          onChange={event => { setQuestion(event.target.value); setFailedRun(null); setRunError(''); }} onKeyDown={onComposerKey}/>
        <button className="send-button" type="submit" disabled={creatingChat || !question.trim() || !!run || historyLoading || listLoading} aria-label="Отправить вопрос">{run || creatingChat ? <span className="spinner"/> : <Icon name="arrow" size={21}/>}</button>
      </form><div className="composer-foot"><span>Enter - отправить · Shift + Enter - новая строка</span><span>{question.length ? `${question.length} / 4000` : 'Проверяйте важные детали по источникам'}</span></div>
    </footer></main>
    {citation && <SourceDialog key={citation.chunk_identifier} api={api} citation={citation} onClose={() => setCitation(null)}/>}
    {chatAction && <ManagementDialog title={chatAction.kind === 'rename' ? 'Название чата' : 'Удалить чат?'} initialTitle={chatAction.chat.title_text}
      destructive={chatAction.kind === 'delete'} description={chatAction.kind === 'delete' ? `Переписка «${chatAction.chat.title_text}» будет удалена. Материалы проекта сохранятся` : undefined}
      onClose={() => setChatAction(null)} onConfirm={async (title, signal) => {
        const current = scope.current.session();
        if (chatAction.kind === 'rename') {
          const updated = await api.renameChat(chatAction.chat.chat_identifier, title, signal);
          if (current() && !signal.aborted) setChats(previous => previous.map(item => item.chat_identifier === updated.chat_identifier ? updated : item));
        } else {
          await api.deleteChat(chatAction.chat.chat_identifier, signal);
          if (current() && !signal.aborted) {
            setChats(previous => previous.filter(item => item.chat_identifier !== chatAction.chat.chat_identifier));
            if (selectedChat.current === chatAction.chat.chat_identifier) newChat();
          }
        }
      }}/>}
  </div>;
}

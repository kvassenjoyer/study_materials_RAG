import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiClient, errorText, isAbort } from './api';
import type { Identity } from './types';
import { AuthForm } from './components/AuthForm';
import { Icon } from './components/Icon';
import { Workspace } from './Workspace';
import { ThemeToggle } from './components/ThemeToggle';

const storageKey = 'study-rag.session';
function readToken(): string | null { try { return sessionStorage.getItem(storageKey); } catch { return null; } }

export function App() {
  const [token, setToken] = useState<string | null>(readToken);
  const tokenRef = useRef(token);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [notice, setNotice] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [attempt, setAttempt] = useState(0);

  const changeToken = useCallback((next: string | null) => {
    tokenRef.current = next; setIdentity(null); setConnectionError(''); setToken(next);
    try { if (next) sessionStorage.setItem(storageKey, next); else sessionStorage.removeItem(storageKey); } catch { /* Memory-only session still works. */ }
  }, []);
  const api = useMemo(() => new ApiClient(token, () => {
    if (tokenRef.current === token) { changeToken(null); setNotice('Сессия истекла. Войдите ещё раз'); }
  }), [token, changeToken]);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    void api.identity(controller.signal).then(result => {
      if (!controller.signal.aborted && tokenRef.current === token) { setIdentity(result); setNotice(''); }
    }).catch(error => {
      if (!controller.signal.aborted && tokenRef.current === token && !isAbort(error)) setConnectionError(errorText(error));
    });
    return () => controller.abort();
  }, [api, token, attempt]);

  if (!token) return <AuthForm onToken={changeToken} notice={notice}/>;
  if (!identity) return <main className="boot-screen"><div className="boot-theme"><ThemeToggle/></div><span className="brand-icon"><Icon name="book" size={26}/></span>
    {connectionError ? <><p role="alert">{connectionError}</p><button className="primary-button" onClick={() => { setConnectionError(''); setAttempt(value => value + 1); }}>Повторить</button><button className="text-button" onClick={() => changeToken(null)}>Вернуться ко входу</button></>
      : <p className="loading-line" role="status"><span className="spinner"/>Открываю библиотеку…</p>}</main>;
  return <Workspace key={identity.user_identifier} api={api} token={token} identity={identity} onLogout={() => { setNotice(''); changeToken(null); }}/>;
}

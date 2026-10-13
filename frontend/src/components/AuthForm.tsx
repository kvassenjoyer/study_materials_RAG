import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiClient, errorText, isAbort } from '../api';
import { Icon } from './Icon';

export function AuthForm({ onToken, notice }: { onToken: (token: string) => void; notice: string }) {
  const [register, setRegister] = useState(false);
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return;
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError('');
    try {
      const result = await new ApiClient(null).authenticate(login.trim(), password, register, controller.signal);
      if (!controller.signal.aborted) { setPassword(''); onToken(result.access_token); }
    } catch (error) { if (!controller.signal.aborted && !isAbort(error)) setError(errorText(error)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }

  return <main className="auth-layout"><section className="auth-story"><div className="auth-brand-row"><span className="brand"><Icon name="book"/>Мини-RAG</span></div>
    <div className="auth-intro"><h1>Учитесь с опорой на свои материалы</h1>
      <p>Соберите материалы в одном месте. <br/>Задавайте вопросы и возвращайтесь <br/>к первоисточнику в один клик</p>
      <div className="illustration" aria-hidden="true"><div className="paper paper-back"/><div className="paper paper-front"><Icon name="book" size={42}/><div className="paper-line"/><div className="paper-line short"/><div className="paper-line"/><span className="paper-stamp"><Icon name="check" size={16}/> С источниками</span></div><span className="illustration-spark"><Icon name="spark" size={29}/></span></div>
    </div>
  </section><section className="auth-panel"><div className="auth-card">
    <h2>{register ? 'Ваша новая библиотека' : 'С возвращением'}</h2><p className="muted">{register ? 'Создайте аккаунт, чтобы загрузить первые материалы' : 'Войдите, чтобы продолжить изучение'}</p>
    <div className="auth-tabs" aria-label="Вход или регистрация"><button aria-pressed={!register} onClick={() => { setRegister(false); setError(''); }}>Войти</button><button aria-pressed={register} onClick={() => { setRegister(true); setError(''); }}>Создать аккаунт</button></div>
    <form onSubmit={event => void submit(event)}><label htmlFor="login">Логин</label><input id="login" name="username" type="text" autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="Например, student" minLength={register ? 3 : 1} maxLength={register ? 64 : 320} required value={login} onChange={event => setLogin(event.target.value)} disabled={busy} aria-describedby={register ? 'login-help' : undefined}/>
      {register && <p id="login-help" className="field-help login-help">3-64 символа: буквы, цифры, точка, дефис и подчёркивание</p>}
      <label htmlFor="password">Пароль</label><input id="password" name="password" type="password" autoComplete={register ? 'new-password' : 'current-password'} minLength={12} maxLength={128} required value={password} onChange={event => setPassword(event.target.value)} disabled={busy} aria-describedby="password-help"/>
      <p id="password-help" className="field-help">От 12 до 128 символов</p>
      {(error || notice) && <p className="alert" role="alert">{error || notice}</p>}
      <button className="primary-button auth-submit" type="submit" disabled={busy}>{busy ? <><span className="spinner"/>Подождите…</> : <>{register ? 'Создать аккаунт' : 'Войти в библиотеку'}<Icon name="chevron" size={18}/></>}</button>
    </form><p className="auth-note">TXT, Markdown и PDF · Ответы с цитатами</p>
  </div></section></main>;
}

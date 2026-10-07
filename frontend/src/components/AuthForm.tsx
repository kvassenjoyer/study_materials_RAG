import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiClient, errorText, isAbort } from '../api';
import { Icon } from './Icon';

export function AuthForm({ onToken, notice }: { onToken: (token: string) => void; notice: string }) {
  const [register, setRegister] = useState(false);
  const [email, setEmail] = useState('');
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
      const result = await new ApiClient(null).authenticate(email.trim(), password, register, controller.signal);
      if (!controller.signal.aborted) { setPassword(''); onToken(result.access_token); }
    } catch (error) { if (!controller.signal.aborted && !isAbort(error)) setError(errorText(error)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }

  return <main className="auth-layout"><section className="auth-story"><a href="/" className="brand"><span className="brand-icon"><Icon name="book" size={23}/></span>mini<span>rag</span></a>
    <div className="auth-intro"><p className="eyebrow">ВАША УЧЕБНАЯ БИБЛИОТЕКА</p><h1>Знания ближе,<br/>чем кажется.</h1>
      <p>Соберите материалы в одном месте.<br/>Задавайте вопросы и возвращайтесь<br/>к первоисточнику в один клик.</p>
      <div className="illustration" aria-hidden="true"><div className="paper paper-back"/><div className="paper paper-front"><Icon name="book" size={42}/><div className="paper-line"/><div className="paper-line short"/><div className="paper-line"/><span className="paper-stamp"><Icon name="check" size={16}/> С источниками</span></div><span className="illustration-spark"><Icon name="spark" size={29}/></span></div>
    </div><span className="story-foot">Учитесь с опорой на свои материалы</span>
  </section><section className="auth-panel"><div className="auth-card"><p className="eyebrow">НАЧНЁМ С ПРОСТОГО</p>
    <h2>{register ? 'Ваша новая библиотека' : 'С возвращением'}</h2><p className="muted">{register ? 'Создайте аккаунт, чтобы загрузить первые материалы.' : 'Войдите, чтобы продолжить изучение.'}</p>
    <div className="auth-tabs" aria-label="Вход или регистрация"><button aria-pressed={!register} onClick={() => { setRegister(false); setError(''); }}>Войти</button><button aria-pressed={register} onClick={() => { setRegister(true); setError(''); }}>Создать аккаунт</button></div>
    <form onSubmit={event => void submit(event)}><label htmlFor="email">Email</label><input id="email" name="email" type="email" autoComplete="email" placeholder="you@example.com" required value={email} onChange={event => setEmail(event.target.value)} disabled={busy}/>
      <label htmlFor="password">Пароль</label><input id="password" name="password" type="password" autoComplete={register ? 'new-password' : 'current-password'} minLength={12} maxLength={128} required value={password} onChange={event => setPassword(event.target.value)} disabled={busy} aria-describedby="password-help"/>
      <p id="password-help" className="field-help">От 12 до 128 символов</p>
      {(error || notice) && <p className="alert" role="alert">{error || notice}</p>}
      <button className="primary-button auth-submit" type="submit" disabled={busy}>{busy ? <><span className="spinner"/>Подождите…</> : <>{register ? 'Создать аккаунт' : 'Войти в библиотеку'}<Icon name="chevron" size={18}/></>}</button>
    </form><p className="auth-note">TXT, Markdown и PDF · Ответы с цитатами</p>
  </div></section></main>;
}

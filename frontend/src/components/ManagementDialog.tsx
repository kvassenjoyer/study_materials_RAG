import { useEffect, useRef, useState } from 'react';
import { errorText, isAbort } from '../api';
import { Icon } from './Icon';

export function ManagementDialog({ title, initialTitle = '', description, destructive = false, onConfirm, onClose }: {
  title: string; initialTitle?: string; description?: string; destructive?: boolean;
  onConfirm: (title: string, signal: AbortSignal) => Promise<void>; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const controller = useRef<AbortController | null>(null);
  const [value, setValue] = useState(initialTitle);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const element = dialog.current!; const previous = document.activeElement as HTMLElement | null;
    element.showModal();
    return () => { controller.current?.abort(); element.close(); previous?.focus(); };
  }, []);
  async function confirm() {
    if (busy || (!destructive && !value.trim())) return;
    const request = new AbortController(); controller.current = request; setBusy(true); setError('');
    try { await onConfirm(value.trim(), request.signal); if (!request.signal.aborted) onClose(); }
    catch (error) { if (!request.signal.aborted && !isAbort(error)) setError(errorText(error)); }
    finally { if (!request.signal.aborted) setBusy(false); }
  }
  return <dialog className="management-dialog" ref={dialog} aria-labelledby="management-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}
    onClick={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <form onSubmit={event => { event.preventDefault(); void confirm(); }}>
      <div className="management-heading"><span className={`management-symbol ${destructive ? 'danger' : ''}`}><Icon name={destructive ? 'trash' : 'edit'} size={23}/></span>
        <button type="button" className="icon-button" aria-label="Закрыть" disabled={busy} onClick={onClose}><Icon name="close"/></button></div>
      <h2 id="management-title">{title}</h2>
      {description && <p className="management-description">{description}</p>}
      {!destructive && <label className="management-label">Название<input autoFocus maxLength={200} value={value} disabled={busy}
        onChange={event => setValue(event.target.value)}/></label>}
      {error && <p className="alert" role="alert">{error}</p>}
      <div className="management-actions"><button type="button" className="secondary-button" disabled={busy} onClick={onClose}>Отмена</button>
        <button autoFocus={destructive} type="submit" className={destructive ? 'danger-button' : 'primary-button'} disabled={busy || (!destructive && !value.trim())}>
          {busy ? 'Сохраняю…' : destructive ? 'Удалить' : 'Сохранить'}</button></div>
    </form>
  </dialog>;
}

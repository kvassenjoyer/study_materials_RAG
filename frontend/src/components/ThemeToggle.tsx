import { useEffect, useRef, useState } from 'react';
import { useTheme } from '../theme';
import { Icon } from './Icon';

const options = [
  { value: 'light', label: 'Светлая', icon: 'sun' },
  { value: 'dark', label: 'Тёмная', icon: 'moon' },
  { value: 'system', label: 'Как в системе', icon: 'monitor' },
] as const;

export function ThemeToggle() {
  const { preference, dark, changePreference } = useTheme();
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!container.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);

  return <div className="theme-switcher" ref={container} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <button ref={trigger} className="icon-button theme-toggle" type="button" aria-label="Цветовая тема" aria-expanded={open}
      title={`Цветовая тема: ${options.find(option => option.value === preference)?.label}`} onClick={() => setOpen(value => !value)}>
      <Icon name={dark ? 'moon' : 'sun'} size={19}/>
    </button>
    {open && <div className="theme-options" role="group" aria-label="Выбор цветовой темы">{options.map(option => <button type="button" key={option.value}
      aria-pressed={preference === option.value} onClick={() => { changePreference(option.value); setOpen(false); trigger.current?.focus(); }}>
      <Icon name={option.icon} size={17}/><span>{option.label}</span>{preference === option.value && <Icon name="check" size={15}/>}
    </button>)}</div>}
  </div>;
}

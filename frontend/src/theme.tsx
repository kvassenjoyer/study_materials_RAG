import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

type ThemePreference = 'light' | 'dark' | 'system';
type ThemeContextValue = { preference: ThemePreference; dark: boolean; changePreference: (value: ThemePreference) => void };
const storageKey = 'ragumentation.theme';
const ThemeContext = createContext<ThemeContextValue | null>(null);
const preferenceValue = (value: string | null): ThemePreference => value === 'light' || value === 'dark' ? value : 'system';

function readPreference(): ThemePreference {
  try { return preferenceValue(localStorage.getItem(storageKey)); } catch { return 'system'; }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState<ThemePreference>(readPreference);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches);
  const dark = preference === 'dark' || (preference === 'system' && systemDark);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const changed = () => setSystemDark(media.matches);
    const stored = (event: StorageEvent) => { if (event.key === storageKey || event.key === null) setPreference(readPreference()); };
    changed(); media.addEventListener('change', changed); window.addEventListener('storage', stored);
    return () => { media.removeEventListener('change', changed); window.removeEventListener('storage', stored); };
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#181a21' : '#f8f7f2');
  }, [dark]);

  function changePreference(value: ThemePreference) {
    setPreference(value);
    try { if (value === 'system') localStorage.removeItem(storageKey); else localStorage.setItem(storageKey, value); }
    catch { /* Theme switching still works when browser storage is unavailable. */ }
  }

  return <ThemeContext.Provider value={{ preference, dark, changePreference }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const theme = useContext(ThemeContext);
  if (!theme) throw new Error('ThemeProvider is required');
  return theme;
}

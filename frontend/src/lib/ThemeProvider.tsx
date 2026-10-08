'use client';

import { createContext, useContext, useEffect, useState, useCallback } from 'react';

import {
  APPEARANCE_STORAGE_KEYS,
  applyCornerStyle,
  applyPalette,
  readStoredCornerStyle,
  readStoredPalette,
  storeCornerStyle,
  storePalette,
  type CornerStyle,
  type Palette,
} from '@/lib/appearance';

export type Theme = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

interface ThemeContextValue {
  theme: Theme;
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: Theme) => void;
  palette: Palette;
  setPalette: (palette: Palette) => void;
  cornerStyle: CornerStyle;
  setCornerStyle: (cornerStyle: CornerStyle) => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

const THEME_STORAGE_KEY = APPEARANCE_STORAGE_KEYS.theme;

function getSystemTheme(): ResolvedTheme {
  if (typeof window === 'undefined') return 'dark';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function resolveTheme(theme: Theme): ResolvedTheme {
  if (theme === 'system') {
    return getSystemTheme();
  }
  return theme;
}

function applyTheme(resolvedTheme: ResolvedTheme) {
  const root = document.documentElement;
  if (resolvedTheme === 'dark') {
    root.classList.add('dark');
  } else {
    root.classList.remove('dark');
  }
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>('system');
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>('dark');
  const [mounted, setMounted] = useState(false);
  // Palette and corner style are applied to <html> by the inline script before
  // hydration, so these initialisers only have to agree with it; reading the
  // same storage keeps the settings controls showing the active choice.
  const [palette, setPaletteState] = useState<Palette>(readStoredPalette);
  const [cornerStyle, setCornerStyleState] = useState<CornerStyle>(readStoredCornerStyle);

  // Initialize theme from localStorage on mount
  useEffect(() => {
    const stored = localStorage.getItem(THEME_STORAGE_KEY) as Theme | null;
    const initialTheme = stored || 'system';
    setThemeState(initialTheme);
    const resolved = resolveTheme(initialTheme);
    setResolvedTheme(resolved);
    applyTheme(resolved);
    setMounted(true);
  }, []);

  // Listen for system theme changes
  useEffect(() => {
    if (!mounted) return;

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

    const handleChange = () => {
      if (theme === 'system') {
        const resolved = getSystemTheme();
        setResolvedTheme(resolved);
        applyTheme(resolved);
      }
    };

    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, [mounted, theme]);

  const setTheme = useCallback((newTheme: Theme) => {
    setThemeState(newTheme);
    localStorage.setItem(THEME_STORAGE_KEY, newTheme);
    const resolved = resolveTheme(newTheme);
    setResolvedTheme(resolved);
    applyTheme(resolved);
  }, []);

  const setPalette = useCallback((newPalette: Palette) => {
    setPaletteState(newPalette);
    storePalette(newPalette);
    applyPalette(newPalette);
  }, []);

  const setCornerStyle = useCallback((newCornerStyle: CornerStyle) => {
    setCornerStyleState(newCornerStyle);
    storeCornerStyle(newCornerStyle);
    applyCornerStyle(newCornerStyle);
  }, []);

  // Prevent hydration mismatch by not rendering until mounted
  // The inline script handles initial theme, so no flash occurs
  const value: ThemeContextValue = {
    theme,
    resolvedTheme,
    setTheme,
    palette,
    setPalette,
    cornerStyle,
    setCornerStyle,
  };

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
}

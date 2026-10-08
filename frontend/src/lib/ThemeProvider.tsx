'use client';

import { createContext, useContext, useEffect, useState, useCallback } from 'react';

import {
  APPEARANCE_STORAGE_KEYS,
  applyCornerStyle,
  applyPalette,
  readStoredCornerStyle,
  readStoredPalette,
  readStoredTheme,
  storeCornerStyle,
  storePalette,
  storeTheme,
  type CornerStyle,
  type Palette,
  type Theme,
} from '@/lib/appearance';

export type { Theme } from '@/lib/appearance';
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

  // Initialize theme from localStorage on mount. readStoredTheme validates
  // against the allowed values and survives blocked storage, so a junk value
  // falls back to System rather than reaching the select or the class list.
  useEffect(() => {
    const initialTheme = readStoredTheme();
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

  // Another tab changed an appearance setting. The storage event fires only in
  // the other tabs of this origin, so re-reading (and re-validating) storage
  // here keeps every open tab on the same look. A null key means storage was
  // cleared, which returns everything to its default.
  useEffect(() => {
    const keys = APPEARANCE_STORAGE_KEYS;
    const handleStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === keys.theme) {
        const nextTheme = readStoredTheme();
        const resolved = resolveTheme(nextTheme);
        setThemeState(nextTheme);
        setResolvedTheme(resolved);
        applyTheme(resolved);
      }
      if (event.key === null || event.key === keys.palette) {
        const nextPalette = readStoredPalette();
        setPaletteState(nextPalette);
        applyPalette(nextPalette);
      }
      if (event.key === null || event.key === keys.corners) {
        const nextCorners = readStoredCornerStyle();
        setCornerStyleState(nextCorners);
        applyCornerStyle(nextCorners);
      }
    };

    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  const setTheme = useCallback((newTheme: Theme) => {
    setThemeState(newTheme);
    storeTheme(newTheme);
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

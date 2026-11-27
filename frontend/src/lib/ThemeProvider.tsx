'use client';

import { createContext, useContext, useEffect, useState, useCallback } from 'react';

import {
  APPEARANCE_STORAGE_KEYS,
  applyCornerStyle,
  applyPalette,
  applyUiStyle,
  readStoredCornerPreference,
  readStoredPalette,
  readStoredTheme,
  readStoredUiStyle,
  resolveCornerStyle,
  storeCornerPreference,
  storePalette,
  storeTheme,
  storeUiStyle,
  type CornerPreference,
  type CornerStyle,
  type Palette,
  type Theme,
  type UiStyle,
} from '@/lib/appearance';

export type { Theme } from '@/lib/appearance';
export type ResolvedTheme = 'light' | 'dark';

interface ThemeContextValue {
  theme: Theme;
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: Theme) => void;
  palette: Palette;
  setPalette: (palette: Palette) => void;
  /** The Corner style setting, Automatic included. */
  cornerPreference: CornerPreference;
  setCornerPreference: (cornerPreference: CornerPreference) => void;
  /** The corners that show: the setting, or under Automatic the style's own. */
  cornerStyle: CornerStyle;
  uiStyle: UiStyle;
  setUiStyle: (uiStyle: UiStyle) => void;
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
  // Palette, corners and style are applied to <html> by the inline script before
  // hydration, so these initialisers only have to agree with it; reading the
  // same storage keeps the settings controls showing the active choice.
  const [palette, setPaletteState] = useState<Palette>(readStoredPalette);
  const [cornerPreference, setCornerPreferenceState] = useState<CornerPreference>(readStoredCornerPreference);
  const [uiStyle, setUiStyleState] = useState<UiStyle>(readStoredUiStyle);
  const cornerStyle = resolveCornerStyle(cornerPreference, uiStyle);

  // Corners follow both settings: Automatic takes them from the style, so a
  // change of either, here or in another tab, can change them.
  useEffect(() => {
    applyCornerStyle(cornerStyle);
  }, [cornerStyle]);

  // Initialize theme from localStorage on mount. readStoredTheme validates
  // against the allowed values and survives blocked storage, so a junk value
  // falls back to System rather than reaching the select or the class list.
  useEffect(() => {
    const initialTheme = readStoredTheme();
    applyUiStyle(readStoredUiStyle());
    // eslint-disable-next-line react-hooks/set-state-in-effect -- storage is read after hydration so the server and the client render the same tree; the theme control shows this value
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
        setCornerPreferenceState(readStoredCornerPreference());
      }
      if (event.key === null || event.key === keys.style) {
        const nextStyle = readStoredUiStyle();
        setUiStyleState(nextStyle);
        applyUiStyle(nextStyle);
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

  const setCornerPreference = useCallback((newPreference: CornerPreference) => {
    setCornerPreferenceState(newPreference);
    storeCornerPreference(newPreference);
  }, []);

  const setUiStyle = useCallback((newStyle: UiStyle) => {
    setUiStyleState(newStyle);
    storeUiStyle(newStyle);
    applyUiStyle(newStyle);
  }, []);

  // Children render before mount: the inline script has already applied the
  // stored appearance, so nothing flashes.
  const value: ThemeContextValue = {
    theme,
    resolvedTheme,
    setTheme,
    palette,
    setPalette,
    cornerPreference,
    setCornerPreference,
    cornerStyle,
    uiStyle,
    setUiStyle,
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

// Inline script to prevent flash of unstyled content (FOUC)
// This runs before React hydration to apply the correct theme immediately

export const themeScript = `
(function() {
  const THEME_STORAGE_KEY = 'nojoin-theme';
  
  function getSystemTheme() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    const theme = stored || 'system';
    const resolved = theme === 'system' ? getSystemTheme() : theme;
    
    if (resolved === 'dark') {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  } catch (e) {
    // localStorage may be unavailable, default to system preference
    if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
      document.documentElement.classList.add('dark');
    }
  }
})();
`;

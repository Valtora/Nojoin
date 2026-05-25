const GITHUB_REPO = 'Valtora/Nojoin';

export type Platform = 'windows' | 'unknown';
export type BrowserFamily = 'firefox' | 'other';

export function detectPlatform(): Platform {
  if (typeof window === 'undefined') {
    return 'unknown';
  }

  const userAgent = window.navigator.userAgent.toLowerCase();
  const platform = window.navigator.platform?.toLowerCase() || '';

  // Check for Windows
  if (userAgent.includes('win') || platform.includes('win')) {
    return 'windows';
  }

  // macOS and Linux are not currently supported for the companion app
  return 'unknown';
}

export function getDownloadUrl(): string {
  // Direct to releases page where users can download the Windows installer
  return `https://github.com/${GITHUB_REPO}/releases/latest`;
}

export function detectBrowserFamily(): BrowserFamily {
  if (typeof window === 'undefined') {
    return 'other';
  }

  const userAgent = window.navigator.userAgent.toLowerCase();
  return userAgent.includes('firefox') ? 'firefox' : 'other';
}

export function getDownloadLabel(): string {
  return 'Download Companion';
}

export function getPlatformIcon(platform: Platform = detectPlatform()): string {
  switch (platform) {
    case 'windows':
      return 'windows';
    default:
      return 'download';
  }
}



const GITHUB_REPO = 'Valtora/Nojoin';

export type Platform = 'windows' | 'macos' | 'linux' | 'unknown';

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

  // Check for macOS
  if (userAgent.includes('mac') || platform.includes('mac')) {
    return 'macos';
  }

  // Check for Linux (but not Android)
  if ((userAgent.includes('linux') || platform.includes('linux')) && !userAgent.includes('android')) {
    return 'linux';
  }

  return 'unknown';
}

export function getDownloadUrl(): string {
  // Robustness: Directing to the releases page is safer than hardcoding filenames
  // which change with every version (e.g. including version numbers) and architecture.
  // This allows the user to choose the correct installer (e.g. Apple Silicon vs Intel).
  return `https://github.com/${GITHUB_REPO}/releases/latest`;
}

export function getDownloadLabel(): string {
  return 'Download Companion';
}

export function getPlatformIcon(platform: Platform = detectPlatform()): string {
  switch (platform) {
    case 'windows':
      return 'windows';
    case 'macos':
      return 'apple';
    case 'linux':
      return 'linux';
    default:
      return 'download';
  }
}



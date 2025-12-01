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

export function getDownloadUrl(platform: Platform = detectPlatform()): string {
  const baseUrl = `https://github.com/${GITHUB_REPO}/releases/latest/download`;
  
  switch (platform) {
    case 'windows':
      return `${baseUrl}/NojoinCompanion-Setup.exe`;
    case 'macos':
      return `${baseUrl}/NojoinCompanion.dmg`;
    case 'linux':
      return `${baseUrl}/NojoinCompanion.AppImage`;
    default:
      // Default to releases page if unknown
      return `https://github.com/${GITHUB_REPO}/releases/latest`;
  }
}

export function getDownloadLabel(platform: Platform = detectPlatform()): string {
  switch (platform) {
    case 'windows':
      return 'Download for Windows';
    case 'macos':
      return 'Download for macOS';
    case 'linux':
      return 'Download for Linux';
    default:
      return 'Download Companion';
  }
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



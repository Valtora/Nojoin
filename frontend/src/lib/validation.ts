export const sanitizeIntegerString = (value: string, min?: number, max?: number): string => {
  // Remove non-digit characters
  let digits = value.replace(/\D/g, '');
  // Remove leading zeros unless the value is just "0"
  if (digits.length > 1 && digits.startsWith('0')) {
    digits = parseInt(digits, 10).toString();
  }

  if (digits === '') return '';

  if (max !== undefined) {
    const num = parseInt(digits, 10);
    if (num > max) {
      return max.toString();
    }
  }

  return digits;
};

export const clampNumber = (value: number, min: number, max: number): number => {
  return Math.min(Math.max(value, min), max);
};

export const trimString = (value: string): string => {
  return value.trim();
};

export const isValidUrl = (url: string): boolean => {
  try {
    new URL(url);
    return true;
  } catch {
    return false;
  }
};

export const sanitizeUrl = (url: string): string => {
  const trimmed = url.trim();
  if (!trimmed) return '';
  // Basic check to ensure protocol is present, if not add https://
  if (!/^https?:\/\//i.test(trimmed)) {
    return `https://${trimmed}`;
  }
  return trimmed;
};

export const validateApiKeyFormat = (key: string, provider: string): boolean => {
  const trimmed = key.trim();
  if (!trimmed) return true; // Allow empty if optional, handled by required checks elsewhere

  switch (provider) {
    case 'openai':
      return trimmed.startsWith('sk-');
    case 'anthropic':
      return trimmed.startsWith('sk-ant-');
    // Add other provider specific checks if known
    default:
      return true;
  }
};

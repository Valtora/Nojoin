import type { CaptureSupport } from "./shared";

interface UserAgentBrand {
  brand: string;
  version: string;
}

interface UserAgentDataLike {
  brands?: UserAgentBrand[];
  mobile?: boolean;
  platform?: string;
}

export interface FeatureDetectEnvironment {
  userAgent?: string;
  userAgentData?: UserAgentDataLike;
  mediaDevices?: {
    getDisplayMedia?: MediaDevices["getDisplayMedia"];
  } | null;
}

const CHROMIUM_BRANDS = [
  "chromium",
  "google chrome",
  "microsoft edge",
  "brave",
  "arc",
];

const isMobileUserAgent = (userAgent: string) =>
  /android|iphone|ipad|ipod|mobile/i.test(userAgent);

const isFirefoxUserAgent = (userAgent: string) => /firefox/i.test(userAgent);

const isSafariUserAgent = (userAgent: string) => {
  return /safari/i.test(userAgent) && !/chrome|chromium|crios|edg|opr/i.test(userAgent);
};

const isChromiumUserAgent = (userAgent: string) =>
  /chrome|chromium|crios|edg|brave|arc/i.test(userAgent);

const isMacPlatform = (platform: string) => /mac/i.test(platform);

const isWindowsOrLinuxPlatform = (platform: string) =>
  /win|windows|linux/i.test(platform);

const hasChromiumBrand = (brands: UserAgentBrand[] | undefined) => {
  if (!brands || brands.length === 0) {
    return false;
  }

  return brands.some((entry) =>
    CHROMIUM_BRANDS.includes(entry.brand.toLowerCase()),
  );
};

const readNavigatorEnvironment = (): FeatureDetectEnvironment => {
  if (typeof navigator === "undefined") {
    return { mediaDevices: null };
  }

  const navigatorWithUAData = navigator as Navigator & {
    userAgentData?: UserAgentDataLike;
  };

  return {
    userAgent: navigator.userAgent,
    userAgentData: navigatorWithUAData.userAgentData,
    mediaDevices: navigator.mediaDevices,
  };
};

export const detectCaptureSupport = (
  environment: FeatureDetectEnvironment = readNavigatorEnvironment(),
): CaptureSupport => {
  const userAgent = (environment.userAgent || "").toLowerCase();
  const userAgentData = environment.userAgentData;
  const platform = (userAgentData?.platform || userAgent).toLowerCase();
  const mediaDevices = environment.mediaDevices;
  const mobile = Boolean(userAgentData?.mobile) || isMobileUserAgent(userAgent);
  const firefox = isFirefoxUserAgent(userAgent);
  const safari = isSafariUserAgent(userAgent);
  const chromium =
    hasChromiumBrand(userAgentData?.brands) || isChromiumUserAgent(userAgent);
  const macPlatform = isMacPlatform(platform);
  const supportedPlatform = isWindowsOrLinuxPlatform(platform);
  const hasDisplayMedia = Boolean(mediaDevices?.getDisplayMedia);

  if (mobile) {
    return { supported: false, reason: "mobile" };
  }

  if (firefox) {
    return { supported: false, reason: "firefox" };
  }

  if (safari) {
    return { supported: false, reason: "safari" };
  }

  if (chromium && macPlatform) {
    return { supported: false, reason: "macos_chromium" };
  }

  if (chromium && supportedPlatform && hasDisplayMedia) {
    return { supported: true };
  }

  return { supported: false, reason: "unknown" };
};
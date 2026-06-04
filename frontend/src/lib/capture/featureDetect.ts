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
    getUserMedia?: MediaDevices["getUserMedia"];
  } | null;
  mediaRecorderCtor?: typeof MediaRecorder | null;
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

const isChromeMobileUserAgent = (userAgent: string) =>
  (/android/i.test(userAgent) &&
    /chrome/i.test(userAgent) &&
    !/edg|opr|firefox/i.test(userAgent)) ||
  /crios/i.test(userAgent);

const isDesktopCapturePlatform = (platform: string) =>
  /win|windows|linux|mac/i.test(platform);

const hasChromiumBrand = (brands: UserAgentBrand[] | undefined) => {
  if (!brands || brands.length === 0) {
    return false;
  }

  return brands.some((entry) =>
    CHROMIUM_BRANDS.includes(entry.brand.toLowerCase()),
  );
};

const hasChromeBrand = (brands: UserAgentBrand[] | undefined) => {
  if (!brands || brands.length === 0) {
    return false;
  }

  return brands.some((entry) => entry.brand.toLowerCase() === "google chrome");
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
    mediaRecorderCtor:
      typeof MediaRecorder === "undefined" ? null : MediaRecorder,
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
  const supportedPlatform = isDesktopCapturePlatform(platform);
  const hasDisplayMedia = Boolean(mediaDevices?.getDisplayMedia);
  const hasUserMedia = Boolean(mediaDevices?.getUserMedia);
  const hasMediaRecorder = Boolean(environment.mediaRecorderCtor);
  const chromeMobile =
    mobile &&
    (hasChromeBrand(userAgentData?.brands) || isChromeMobileUserAgent(userAgent));

  if (chromeMobile && hasUserMedia && hasMediaRecorder) {
    return { supported: true, mode: "microphone_only" };
  }

  if (mobile) {
    return { supported: false, reason: "mobile" };
  }

  if (firefox) {
    return { supported: false, reason: "firefox" };
  }

  if (safari) {
    return { supported: false, reason: "safari" };
  }

  if (
    chromium &&
    supportedPlatform &&
    hasDisplayMedia &&
    hasUserMedia &&
    hasMediaRecorder
  ) {
    return { supported: true, mode: "shared_audio" };
  }

  return { supported: false, reason: "unknown" };
};

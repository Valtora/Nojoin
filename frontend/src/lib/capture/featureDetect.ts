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
  /** Capture records through an AudioWorklet tap on the mixer. */
  audioWorkletNodeCtor?: typeof AudioWorkletNode | null;
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

const isChromiumUserAgent = (userAgent: string) =>
  /chrome|chromium|crios|edg|brave|arc/i.test(userAgent);

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
    audioWorkletNodeCtor:
      typeof AudioWorkletNode === "undefined" ? null : AudioWorkletNode,
  };
};

export const detectCaptureSupport = (
  environment: FeatureDetectEnvironment = readNavigatorEnvironment(),
): CaptureSupport => {
  const userAgent = (environment.userAgent || "").toLowerCase();
  const userAgentData = environment.userAgentData;
  const platform = (userAgentData?.platform || userAgent).toLowerCase();
  const mediaDevices = environment.mediaDevices;

  // Every capture records the microphone through an AudioWorklet tap.
  if (!mediaDevices?.getUserMedia || !environment.audioWorkletNodeCtor) {
    return { supported: false };
  }

  // No browser API reports whether a share carries audio, so the browser is
  // recognised instead: only desktop Chromium shares tab or system audio.
  // Firefox and Safari share a picture without its sound, and phones cannot
  // share at all, so every other browser records the microphone on its own.
  const mobile = Boolean(userAgentData?.mobile) || isMobileUserAgent(userAgent);
  const chromium =
    hasChromiumBrand(userAgentData?.brands) || isChromiumUserAgent(userAgent);
  if (
    chromium &&
    !mobile &&
    isDesktopCapturePlatform(platform) &&
    mediaDevices.getDisplayMedia
  ) {
    return { supported: true, mode: "shared_audio" };
  }

  return { supported: true, mode: "microphone_only" };
};

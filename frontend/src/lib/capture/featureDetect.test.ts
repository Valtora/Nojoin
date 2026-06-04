import { describe, expect, it } from "vitest";

import { detectCaptureSupport } from "./featureDetect";

const displayMedia = (() => Promise.resolve({})) as MediaDevices["getDisplayMedia"];
const userMedia = (() => Promise.resolve({})) as MediaDevices["getUserMedia"];
const mediaRecorderCtor = class {
  static isTypeSupported() {
    return true;
  }
} as unknown as typeof MediaRecorder;

describe("capture feature detection", () => {
  it("detects supported Chromium on Windows", () => {
    expect(
      detectCaptureSupport({
        userAgent:
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        userAgentData: {
          brands: [{ brand: "Google Chrome", version: "136" }],
          mobile: false,
          platform: "Windows",
        },
        mediaDevices: { getDisplayMedia: displayMedia, getUserMedia: userMedia },
        mediaRecorderCtor,
      }),
    ).toEqual({ supported: true, mode: "shared_audio" });
  });

  it("detects Chrome on macOS as shared-audio capture", () => {
    expect(
      detectCaptureSupport({
        userAgent:
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        userAgentData: {
          brands: [{ brand: "Google Chrome", version: "141" }],
          mobile: false,
          platform: "macOS",
        },
        mediaDevices: { getDisplayMedia: displayMedia, getUserMedia: userMedia },
        mediaRecorderCtor,
      }),
    ).toEqual({ supported: true, mode: "shared_audio" });
  });

  it("allows non-Chrome Chromium browsers on macOS as best-effort shared-audio capture", () => {
    expect(
      detectCaptureSupport({
        userAgent:
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/537.36 (KHTML, like Gecko) Edg/141.0.0.0 Safari/537.36",
        userAgentData: {
          brands: [{ brand: "Microsoft Edge", version: "141" }],
          mobile: false,
          platform: "macOS",
        },
        mediaDevices: { getDisplayMedia: displayMedia, getUserMedia: userMedia },
        mediaRecorderCtor,
      }),
    ).toEqual({ supported: true, mode: "shared_audio" });
  });

  it.each([
    {
      name: "Firefox",
      environment: {
        userAgent:
          "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
        mediaDevices: { getDisplayMedia: displayMedia, getUserMedia: userMedia },
        mediaRecorderCtor,
      },
      expected: { supported: false, reason: "firefox" },
    },
    {
      name: "Safari",
      environment: {
        userAgent:
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        mediaDevices: { getDisplayMedia: displayMedia, getUserMedia: userMedia },
        mediaRecorderCtor,
      },
      expected: { supported: false, reason: "safari" },
    },
    {
      name: "unsupported mobile browser",
      environment: {
        userAgent:
          "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) EdgA/136.0.0.0 Mobile Safari/537.36",
        userAgentData: {
          brands: [{ brand: "Microsoft Edge", version: "136" }],
          mobile: true,
          platform: "Android",
        },
        mediaDevices: { getDisplayMedia: displayMedia, getUserMedia: userMedia },
        mediaRecorderCtor,
      },
      expected: { supported: false, reason: "mobile" },
    },
    {
      name: "unknown unsupported browser",
      environment: {
        userAgent: "CustomBrowser/1.0",
        userAgentData: {
          brands: [{ brand: "Custom Browser", version: "1" }],
          mobile: false,
          platform: "Linux",
        },
        mediaDevices: {},
        mediaRecorderCtor,
      },
      expected: { supported: false, reason: "unknown" },
    },
  ])("tags $name as unsupported", ({ environment, expected }) => {
    expect(detectCaptureSupport(environment)).toEqual(expected);
  });

  it("detects Chrome Android as microphone-only capture", () => {
    expect(
      detectCaptureSupport({
        userAgent:
          "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36",
        userAgentData: {
          brands: [{ brand: "Google Chrome", version: "136" }],
          mobile: true,
          platform: "Android",
        },
        mediaDevices: { getUserMedia: userMedia },
        mediaRecorderCtor,
      }),
    ).toEqual({ supported: true, mode: "microphone_only" });
  });

  it("detects Chrome iOS as microphone-only capture", () => {
    expect(
      detectCaptureSupport({
        userAgent:
          "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/136.0.0.0 Mobile/15E148 Safari/604.1",
        mediaDevices: { getUserMedia: userMedia },
        mediaRecorderCtor,
      }),
    ).toEqual({ supported: true, mode: "microphone_only" });
  });
});

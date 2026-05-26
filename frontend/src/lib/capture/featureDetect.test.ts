import { describe, expect, it } from "vitest";

import { detectCaptureSupport } from "./featureDetect";

const displayMedia = (() => Promise.resolve({})) as MediaDevices["getDisplayMedia"];

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
        mediaDevices: { getDisplayMedia: displayMedia },
      }),
    ).toEqual({ supported: true });
  });

  it.each([
    {
      name: "Firefox",
      environment: {
        userAgent:
          "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
        mediaDevices: { getDisplayMedia: displayMedia },
      },
      expected: { supported: false, reason: "firefox" },
    },
    {
      name: "Safari",
      environment: {
        userAgent:
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        mediaDevices: { getDisplayMedia: displayMedia },
      },
      expected: { supported: false, reason: "safari" },
    },
    {
      name: "macOS Chromium",
      environment: {
        userAgent:
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        userAgentData: {
          brands: [{ brand: "Chromium", version: "136" }],
          mobile: false,
          platform: "macOS",
        },
        mediaDevices: { getDisplayMedia: displayMedia },
      },
      expected: { supported: false, reason: "macos_chromium" },
    },
    {
      name: "mobile browser",
      environment: {
        userAgent:
          "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36",
        userAgentData: {
          brands: [{ brand: "Google Chrome", version: "136" }],
          mobile: true,
          platform: "Android",
        },
        mediaDevices: { getDisplayMedia: displayMedia },
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
      },
      expected: { supported: false, reason: "unknown" },
    },
  ])("tags $name as unsupported", ({ environment, expected }) => {
    expect(detectCaptureSupport(environment)).toEqual(expected);
  });
});
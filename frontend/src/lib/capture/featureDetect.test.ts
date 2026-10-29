// @vitest-environment node

import { describe, expect, it } from "vitest";

import { detectCaptureSupport } from "./featureDetect";

const displayMedia = (() => Promise.resolve({})) as MediaDevices["getDisplayMedia"];
const userMedia = (() => Promise.resolve({})) as MediaDevices["getUserMedia"];
const audioWorkletNodeCtor = class {} as unknown as typeof AudioWorkletNode;

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
        audioWorkletNodeCtor,
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
        audioWorkletNodeCtor,
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
        audioWorkletNodeCtor,
      }),
    ).toEqual({ supported: true, mode: "shared_audio" });
  });

  // Real browsers expose getDisplayMedia on desktop, but only Chromium's share
  // carries audio, so these record the microphone rather than being refused.
  it.each([
    {
      name: "Firefox on Linux",
      userAgent: "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
      displayMedia: true,
    },
    {
      name: "Firefox on Windows",
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
      displayMedia: true,
    },
    {
      name: "Firefox on macOS",
      userAgent:
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:131.0) Gecko/20100101 Firefox/131.0",
      displayMedia: true,
    },
    {
      name: "Safari on macOS",
      userAgent:
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
      displayMedia: true,
    },
    {
      name: "Firefox on Android",
      userAgent: "Mozilla/5.0 (Android 14; Mobile; rv:131.0) Gecko/131.0 Firefox/131.0",
      displayMedia: false,
    },
    {
      name: "Safari on iPhone",
      userAgent:
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
      displayMedia: false,
    },
    {
      name: "Firefox on iPhone",
      userAgent:
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/131.0 Mobile/15E148 Safari/605.1.15",
      displayMedia: false,
    },
  ])("records only the microphone in $name", ({ userAgent, displayMedia: hasDisplayMedia }) => {
    expect(
      detectCaptureSupport({
        userAgent,
        mediaDevices: hasDisplayMedia
          ? { getDisplayMedia: displayMedia, getUserMedia: userMedia }
          : { getUserMedia: userMedia },
        audioWorkletNodeCtor,
      }),
    ).toEqual({ supported: true, mode: "microphone_only" });
  });

  it("records only the microphone in a mobile Chromium browser other than Chrome", () => {
    expect(
      detectCaptureSupport({
        userAgent:
          "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) EdgA/136.0.0.0 Mobile Safari/537.36",
        userAgentData: {
          brands: [{ brand: "Microsoft Edge", version: "136" }],
          mobile: true,
          platform: "Android",
        },
        mediaDevices: { getDisplayMedia: displayMedia, getUserMedia: userMedia },
        audioWorkletNodeCtor,
      }),
    ).toEqual({ supported: true, mode: "microphone_only" });
  });

  it.each([
    {
      name: "Firefox without the capture worklet",
      environment: {
        userAgent: "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
        mediaDevices: { getDisplayMedia: displayMedia, getUserMedia: userMedia },
        audioWorkletNodeCtor: null,
      },
    },
    {
      // A plain-HTTP origin hides navigator.mediaDevices altogether.
      name: "Safari on an insecure origin",
      environment: {
        userAgent:
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
        mediaDevices: undefined,
        audioWorkletNodeCtor,
      },
    },
    {
      name: "a browser without microphone access",
      environment: {
        userAgent: "CustomBrowser/1.0",
        userAgentData: {
          brands: [{ brand: "Custom Browser", version: "1" }],
          mobile: false,
          platform: "Linux",
        },
        mediaDevices: {},
        audioWorkletNodeCtor,
      },
    },
  ])("refuses $name", ({ environment }) => {
    expect(detectCaptureSupport(environment)).toEqual({ supported: false });
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
        audioWorkletNodeCtor,
      }),
    ).toEqual({ supported: true, mode: "microphone_only" });
  });

  it("detects Chrome iOS as microphone-only capture", () => {
    expect(
      detectCaptureSupport({
        userAgent:
          "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/136.0.0.0 Mobile/15E148 Safari/604.1",
        mediaDevices: { getUserMedia: userMedia },
        audioWorkletNodeCtor,
      }),
    ).toEqual({ supported: true, mode: "microphone_only" });
  });

  it("refuses a browser that cannot run the capture worklet", () => {
    // Capture takes its audio from an AudioWorklet, so without one there is
    // nothing to record however capable the rest of the browser is.
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
        audioWorkletNodeCtor: null,
      }),
    ).toEqual({ supported: false });
  });
});

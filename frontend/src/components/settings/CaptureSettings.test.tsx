import { useSyncExternalStore } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AUDIO_START_TIMEOUT_MS } from "@/lib/capture/audioStart";
import { CaptureController } from "@/lib/capture/controller";
import { audioTrack, mediaStream } from "@/lib/capture/releaseMicrophone.fixtures";
import type { CaptureSupport } from "@/lib/capture/shared";
import CaptureSettings from "./CaptureSettings";

let controller: CaptureController;

vi.mock("@/lib/capture/featureDetect", () => ({
  detectCaptureSupport: (): CaptureSupport => ({ supported: true, mode: "shared_audio" }),
}));

// Keep the real controller/store/settings persistence; avoid provider lifecycle
// network calls, which are unrelated to configuring this browser.
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    ...useSyncExternalStore(controller.subscribe, controller.getState, controller.getState),
    updateSettings: controller.updateSettings,
  }),
}));

beforeEach(() => {
  vi.stubGlobal("navigator", {
    mediaDevices: {
      enumerateDevices: vi.fn().mockResolvedValue([]),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    },
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("microphone release setting", () => {
  it("starts off and keeps the choice for the next page load", async () => {
    window.localStorage.clear();
    controller = new CaptureController();
    const view = render(<CaptureSettings />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh devices" })).toBeEnabled());
    const toggle = screen.getByRole("switch", { name: "Release the microphone while paused" });
    expect(toggle).not.toBeChecked();
    expect(toggle).toHaveAccessibleDescription(/this browser.*shared audio/i);

    fireEvent.click(toggle);
    view.unmount();

    controller = new CaptureController();
    render(<CaptureSettings />);
    expect(screen.getByRole("switch", { name: "Release the microphone while paused" })).toBeChecked();
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh devices" })).toBeEnabled());
  });
});

describe("the microphone input test (issue #925)", () => {
  const audioNode = () => ({ connect: vi.fn(), disconnect: vi.fn() });

  /** The input test's audio context, which this browser starts or never does. */
  class PreviewAudioContext {
    static instances: PreviewAudioContext[] = [];

    static startsAudio = true;

    state: AudioContextState = "suspended";

    constructor() {
      PreviewAudioContext.instances.push(this);
    }

    // Headless Firefox with no sound server leaves resume() pending for ever.
    resume = vi.fn(() => {
      if (!PreviewAudioContext.startsAudio) return new Promise<void>(() => {});
      this.state = "running";
      return Promise.resolve();
    });

    close = vi.fn(async () => {
      this.state = "closed";
    });

    createMediaStreamSource = vi.fn(audioNode);

    createGain = vi.fn(() => ({ ...audioNode(), gain: { value: 1 } }));

    createAnalyser = vi.fn(() => ({
      ...audioNode(),
      fftSize: 2048,
      smoothingTimeConstant: 0,
      getByteTimeDomainData: (samples: Uint8Array) => samples.fill(128),
    }));
  }

  let microphone: MediaStreamTrack;

  beforeEach(() => {
    window.localStorage.clear();
    controller = new CaptureController();
    microphone = audioTrack();
    PreviewAudioContext.instances = [];
    PreviewAudioContext.startsAudio = false;
    vi.stubGlobal("navigator", {
      mediaDevices: {
        enumerateDevices: vi.fn().mockResolvedValue([]),
        getUserMedia: vi.fn(async () => mediaStream(microphone)),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      },
    });
    vi.stubGlobal("AudioContext", PreviewAudioContext);
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.useFakeTimers();
  });

  /** Renders the settings and selects Start input test, leaving it waiting on the browser. */
  const startTheTest = async () => {
    const view = render(<CaptureSettings />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Start input test" }));
    await act(async () => {});
    expect(PreviewAudioContext.instances).toHaveLength(1);
    return { view, context: PreviewAudioContext.instances[0] };
  };

  const callout = () => screen.queryByText(/did not start audio/);

  it("gives up when the browser never starts audio, and releases the microphone", async () => {
    const { context } = await startTheTest();
    expect(screen.getByRole("button", { name: "Stop input test" })).toHaveAttribute("aria-busy", "true");

    await act(() => vi.advanceTimersByTimeAsync(AUDIO_START_TIMEOUT_MS - 1));
    expect(microphone.readyState).toBe("live");

    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(microphone.readyState).toBe("ended");
    expect(context.close).toHaveBeenCalled();
    expect(callout()).toHaveTextContent(
      "The browser did not start audio, so the input test stopped and released the microphone.",
    );
    expect(screen.getByRole("button", { name: "Start input test" })).not.toHaveAttribute("aria-busy");
  });

  it("releases the microphone when turned off while the browser starts audio", async () => {
    const { context } = await startTheTest();

    fireEvent.click(screen.getByRole("button", { name: "Stop input test" }));

    expect(microphone.readyState).toBe("ended");
    expect(context.close).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Start input test" })).not.toHaveAttribute("aria-busy");
    // The start it cancelled says nothing when its time runs out.
    await act(() => vi.advanceTimersByTimeAsync(AUDIO_START_TIMEOUT_MS));
    expect(callout()).not.toBeInTheDocument();
  });

  it("releases the microphone when the page is left while the browser starts audio", async () => {
    const { view, context } = await startTheTest();

    view.unmount();

    expect(microphone.readyState).toBe("ended");
    expect(context.close).toHaveBeenCalled();
  });

  it("runs as before in a browser that starts audio, until turned off", async () => {
    PreviewAudioContext.startsAudio = true;
    const { context } = await startTheTest();

    expect(context.state).toBe("running");
    expect(context.createMediaStreamSource).toHaveBeenCalledOnce();
    expect(microphone.readyState).toBe("live");
    const stop = screen.getByRole("button", { name: "Stop input test" });
    expect(stop).not.toHaveAttribute("aria-busy");

    fireEvent.click(stop);

    expect(microphone.readyState).toBe("ended");
    expect(context.close).toHaveBeenCalled();
  });
});

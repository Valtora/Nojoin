import { useSyncExternalStore } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureController } from "@/lib/capture/controller";
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

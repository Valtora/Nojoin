import { useSyncExternalStore } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureController } from "@/lib/capture/controller";
import type { CaptureSupport } from "@/lib/capture/shared";
import { searchSettingsRegistry } from "./settingsRegistry";
import CaptureSettings from "./CaptureSettings";

let controller: CaptureController;
const environment = vi.hoisted(() => ({
  support: { supported: true, mode: "shared_audio" } as CaptureSupport,
}));

vi.mock("@/lib/capture/featureDetect", () => ({
  detectCaptureSupport: () => environment.support,
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
  it.each(["shared_audio", "microphone_only"] as const)(
    "persists an accessible default-off choice for %s capture across new controllers",
    async (mode) => {
      environment.support = { supported: true, mode };
      controller = new CaptureController();
      expect(controller.getState().support).toEqual({ supported: true, mode });
      const view = render(<CaptureSettings />);
      await waitFor(() => expect(screen.getByRole("button", { name: "Refresh devices" })).toBeEnabled());
      const toggle = screen.getByRole("switch", { name: "Release the microphone while paused" });
      expect(toggle).not.toBeChecked();
      expect(toggle).toHaveAccessibleDescription(/this browser.*shared audio/i);
      expect(screen.getAllByText("This browser")).toHaveLength(1);

      fireEvent.click(toggle);
      expect(toggle).toBeChecked();
      expect(controller.getState().settings).toHaveProperty("releaseMicrophoneOnPause", true);
      view.unmount();

      controller = new CaptureController();
      render(<CaptureSettings />);
      expect(screen.getByRole("switch", { name: "Release the microphone while paused" })).toBeChecked();
      fireEvent.click(screen.getByRole("switch", { name: "Release the microphone while paused" }));
      expect(new CaptureController().getState().settings).toHaveProperty("releaseMicrophoneOnPause", false);
      await waitFor(() => expect(screen.getByRole("button", { name: "Refresh devices" })).toBeEnabled());
    },
  );

  it("finds the browser preference through settings search", () => {
    expect(searchSettingsRegistry("release microphone", { isAdmin: false, isSuperuser: false }))
      .toEqual(expect.arrayContaining([expect.objectContaining({
        entry: expect.objectContaining({
          id: "recording-release-microphone-on-pause", scope: "browser", defaultValue: false,
        }),
      })]));
  });
});

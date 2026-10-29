import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_CAPTURE_SETTINGS } from "@/lib/capture/shared";
import type { Settings } from "@/types";

import CaptureProcessingSettings from "./CaptureProcessingSettings";

const context = vi.hoisted(() => ({
  settings: {} as Settings,
  updateSetting: vi.fn(),
}));

vi.mock("./SettingsProvider", () => ({
  useSettingsContext: () => context,
}));
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({ settings: DEFAULT_CAPTURE_SETTINGS, updateSettings: vi.fn() }),
}));

describe("recording consent notice setting", () => {
  it("turns a notice hidden from the notice itself back on", () => {
    context.settings = { hide_recording_consent_notice: true, enable_vad: false };
    render(<CaptureProcessingSettings />);
    const toggle = screen.getByRole("switch", { name: "Show the notice" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    fireEvent.click(toggle);
    expect(context.updateSetting).toHaveBeenCalledWith({
      hide_recording_consent_notice: false,
      enable_vad: false,
    });
  });
});

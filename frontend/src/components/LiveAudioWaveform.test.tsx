import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import LiveAudioWaveform from "./LiveAudioWaveform";

const capture = {
  runtimeActive: true,
  support: { supported: true, mode: "shared_audio" },
  displayVideoTrack: vi.fn<() => MediaStreamTrack | null>(() => null),
};
let levels = { mixed: 0 };
vi.mock("@/lib/capture/CaptureProvider", () => ({ useCapture: () => capture }));
vi.mock("@/lib/capture/waveform", () => ({ useLiveWaveform: () => levels }));

describe("quiet audio guidance", () => {
  afterEach(() => vi.useRealTimers());

  it("gives microphone advice for a desktop capture with no shared picture", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T12:00:00Z"));
    const view = render(<LiveAudioWaveform recordingId="rec-1" enabled />);
    vi.setSystemTime(new Date("2026-01-01T12:00:21Z"));
    levels = { mixed: 1 };
    view.rerender(<LiveAudioWaveform recordingId="rec-1" enabled />);

    expect(screen.getByText("Audio has been quiet for a bit")).toBeInTheDocument();
    expect(screen.getByText(/keep the microphone near the speaker/)).toBeInTheDocument();
    expect(screen.queryByText(/browser share picker/)).not.toBeInTheDocument();
  });
});

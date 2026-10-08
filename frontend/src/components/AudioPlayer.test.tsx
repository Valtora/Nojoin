import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test/renderWithProviders";
import { RecordingStatus, type Recording } from "@/types";
import { shouldPollRecordingUpdates } from "@/app/(dashboard)/recordings/[id]/_hooks/recordingDetailUtils";

vi.mock("@/lib/api", () => ({
  getRecordingStreamUrl: (id: string) => `/api/v1/recordings/${id}/stream`,
}));

import AudioPlayer from "./AudioPlayer";

const recordingWith = (overrides: Partial<Recording>) =>
  ({
    id: "rec-1",
    name: "Weekly sync",
    status: RecordingStatus.PROCESSED,
    duration_seconds: 60,
    has_proxy: false,
    ...overrides,
  }) as unknown as Recording;

const renderPlayer = (recording: Recording) =>
  renderWithProviders(
    <AudioPlayer
      recording={recording}
      audioRef={{ current: null }}
      currentTime={0}
      onTimeUpdate={vi.fn()}
    />,
  );

describe("a recording with no audio and no proxy", () => {
  it("says the audio is unavailable instead of waiting for processing", () => {
    renderPlayer(recordingWith({ has_audio: false }));

    expect(
      screen.getByText("This recording's audio is not available"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/being processed/)).not.toBeInTheDocument();
    expect(document.querySelector("audio")).toBeNull();
  });

  it("still shows the processing state while a proxy can be made", () => {
    renderPlayer(recordingWith({ has_audio: true }));

    expect(screen.getByText(/being processed/)).toBeInTheDocument();
  });

  it("stops the page polling for a proxy that will never come", () => {
    expect(shouldPollRecordingUpdates(recordingWith({ has_audio: false }))).toBe(
      false,
    );
    expect(shouldPollRecordingUpdates(recordingWith({ has_audio: true }))).toBe(
      true,
    );
  });
});

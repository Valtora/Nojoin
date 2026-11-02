import { describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";
import { RecordingStatus, type Recording } from "@/types";

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

  it("does not call in-flight audio unavailable: the master may not be assembled yet", () => {
    renderPlayer(
      recordingWith({ status: RecordingStatus.PROCESSING, has_audio: false }),
    );

    expect(
      screen.queryByText("This recording's audio is not available"),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/being processed/)).toBeInTheDocument();
  });
});

describe("a recording whose audio fails to load", () => {
  it("says the audio could not be loaded, not that the meeting had none", () => {
    renderPlayer(recordingWith({ has_proxy: true, has_audio: true }));

    const audio = document.querySelector("audio");
    expect(audio).not.toBeNull();
    fireEvent.error(audio as HTMLAudioElement);

    expect(
      screen.getByText("This recording's audio could not be loaded"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/imported with no audio/)).not.toBeInTheDocument();
  });
});

describe("the sample meeting", () => {
  it("says it has no audio, not that it was imported without any", () => {
    renderPlayer(recordingWith({ name: "Welcome to Nojoin" }));

    expect(screen.getByText("This sample meeting has no audio")).toBeInTheDocument();
    expect(screen.queryByText(/imported/)).not.toBeInTheDocument();
  });
});

describe("accessible names (issue #840)", () => {
  const renderPlayable = (compact: boolean) => {
    const audioRef = { current: null as HTMLAudioElement | null };
    const view = renderWithProviders(
      <AudioPlayer
        recording={recordingWith({ has_proxy: true, has_audio: true })}
        audioRef={audioRef}
        currentTime={0}
        onTimeUpdate={vi.fn()}
        compact={compact}
      />,
    );
    return { ...view, audioRef };
  };

  it.each([false, true])("names every control of a playable player (compact: %s)", (compact) => {
    const { audioRef, rerender } = renderPlayable(compact);

    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mute" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Playback speed 1x" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Seek" })).toHaveAttribute("aria-valuetext", "00:00 of 01:00");
    for (const button of screen.getAllByRole("button")) {
      expect(button).toHaveAccessibleName();
    }

    fireEvent.play(audioRef.current as HTMLAudioElement);
    rerender(
      <AudioPlayer
        recording={recordingWith({ has_proxy: true, has_audio: true })}
        audioRef={audioRef}
        currentTime={0}
        onTimeUpdate={vi.fn()}
        compact={compact}
      />,
    );
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mute" }));
    expect(screen.getByRole("button", { name: "Unmute" })).toBeInTheDocument();
  });

  it("names the full player's volume slider", () => {
    renderPlayable(false);

    expect(screen.getByRole("slider", { name: "Volume" })).toBeInTheDocument();
  });

  it.each([
    ["the sample meeting", { name: "Welcome to Nojoin" }],
    ["a recording being processed", { has_audio: true }],
    ["a recording without audio", { has_audio: false }],
  ])("keeps the mock controls of %s out of reach", (_, overrides) => {
    for (const compact of [false, true]) {
      const { unmount } = renderWithProviders(
        <AudioPlayer
          recording={recordingWith(overrides)}
          audioRef={{ current: null }}
          currentTime={0}
          onTimeUpdate={vi.fn()}
          compact={compact}
        />,
      );
      expect(screen.queryAllByRole("button")).toHaveLength(0);
      unmount();
    }
  });
});

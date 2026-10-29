import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";
import { RecordingStatus, type Recording } from "@/types";

vi.mock("@/lib/api", () => ({
  getRecordingStreamUrl: (id: string) => `/api/v1/recordings/${id}/stream`,
  getCapturePlaybackUrl: (id: string, sequence: number) => `/api/v1/recordings/${id}/capture-stream?sequence=${sequence}`,
}));

import AudioPlayer from "./AudioPlayer";

beforeEach(() => { vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {}); });
afterEach(() => { vi.restoreAllMocks(); });

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

it("keeps the audio element through empty capture, growth, an error and final handover", () => {
  const ref = createRef<HTMLAudioElement>();
  const onTimeUpdate = vi.fn();
  const snapshot = recordingWith({
    status: RecordingStatus.UPLOADING, has_proxy: false, capture_playback_sequence: -1,
  });
  const component = (recording: Recording) => <AudioPlayer
    recording={recording} audioRef={ref} currentTime={0} onTimeUpdate={onTimeUpdate} />;
  const view = renderWithProviders(component(snapshot));
  expect(screen.getByText("Waiting for uploaded audio...")).toBeInTheDocument();
  const audio = ref.current!;
  view.rerender(component({ ...snapshot, capture_playback_sequence: 0 }));
  expect(ref.current).toBe(audio);
  fireEvent.loadedMetadata(audio);
  fireEvent.error(audio);
  expect(ref.current).toBe(audio);
  view.rerender(component({ ...snapshot, has_proxy: true, status: RecordingStatus.PROCESSED }));
  fireEvent.loadedMetadata(audio);
  expect(ref.current).toBe(audio);
  expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
});

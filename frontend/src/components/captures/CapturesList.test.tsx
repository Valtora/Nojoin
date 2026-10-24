import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Capture } from "@/lib/api";
import type { PendingCapture } from "@/lib/captures/captureOutbox";
import type { CapturesState } from "@/lib/captures/useCaptures";

import CapturesList from "./CapturesList";

const capture = (id: number, captureMs: number, extra: Partial<Capture> = {}): Capture =>
  ({
    id,
    recording_id: "rec-1",
    kind: "capture",
    capture_ms: captureMs,
    use_in_notes: false,
    title: "Capture",
    file_type: "image/png",
    status: "READY",
    created_at: "2026-10-24T09:00:00",
    ...extra,
  }) as Capture;

const stateOf = (
  captures: Capture[] | null,
  pending: PendingCapture[] = [],
): CapturesState => ({
  captures,
  pending,
  refresh: vi.fn(),
  setUseInNotes: vi.fn(async () => undefined),
  remove: vi.fn(async () => true),
  retry: vi.fn(),
  discard: vi.fn(),
});

describe("the Captures list", () => {
  it("lists captures by time, each with its notes switch, off until turned on", () => {
    const state = stateOf([capture(2, 125_000), capture(1, 65_000, { use_in_notes: true })]);
    render(<CapturesList state={state} onOpen={vi.fn()} />);

    const rows = screen.getAllByTestId("capture-row");
    expect(rows.map((row) => within(row).getByText(/^\d\d:\d\d$/).textContent)).toEqual([
      "01:05",
      "02:05",
    ]);
    const switches = screen.getAllByRole("switch", { name: "Use in notes" });
    expect(switches.map((each) => each.getAttribute("aria-checked"))).toEqual(["true", "false"]);
    expect(screen.getByText("1 of 2 used in notes.")).toBeInTheDocument();

    fireEvent.click(switches[1]);

    expect(state.setUseInNotes).toHaveBeenCalledWith(state.captures![0], true);
  });

  it("opens a capture, jumps to it and plays from it", () => {
    const onOpen = vi.fn();
    const onPlay = vi.fn();
    const onShowInTranscript = vi.fn();
    const saved = capture(1, 65_000);
    render(
      <CapturesList
        state={stateOf([saved])}
        onOpen={onOpen}
        onPlay={onPlay}
        onShowInTranscript={onShowInTranscript}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open the capture at 01:05" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Show the capture at 01:05 in the transcript" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Play from 01:05" }));

    expect(onOpen).toHaveBeenCalledWith(saved);
    expect(onShowInTranscript).toHaveBeenCalledWith(saved);
    expect(onPlay).toHaveBeenCalledWith(65);
  });

  it("says when a capture is still being read", () => {
    render(
      <CapturesList state={stateOf([capture(1, 0, { status: "PROCESSING" })])} onOpen={vi.fn()} />,
    );

    expect(screen.getByText("Reading…")).toBeInTheDocument();
  });

  it("offers Retry and Discard for one that could not be saved", () => {
    const state = stateOf(
      [],
      [
        {
          key: "p-1",
          recordingId: "rec-1",
          captureMs: 3_000,
          preview: null,
          status: "failed",
          error: "Network Error",
        },
      ],
    );
    render(<CapturesList state={state} onOpen={vi.fn()} />);

    expect(screen.getByText("Network Error")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry the capture at 00:03" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard the capture at 00:03" }));

    expect(state.retry).toHaveBeenCalledWith("p-1");
    expect(state.discard).toHaveBeenCalledWith("p-1");
  });

  it("says how to take one when there are none", () => {
    render(<CapturesList state={stateOf([])} onOpen={vi.fn()} />);

    expect(screen.getByText(/Alt\+Shift\+C/)).toBeInTheDocument();
    expect(screen.getByText(/Pasting an image into the note box/)).toBeInTheDocument();
  });
});

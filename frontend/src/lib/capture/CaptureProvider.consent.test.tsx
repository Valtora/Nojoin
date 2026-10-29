import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RecordingConsentNotice from "@/components/RecordingConsentNotice";

import { CaptureProvider, useCapture } from "./CaptureProvider";
import type { CaptureState } from "./shared";

const mocks = vi.hoisted(() => ({
  start: vi.fn(), stop: vi.fn(), save: vi.fn(), settings: vi.fn(),
  state: {} as CaptureState,
  listener: null as ((state: CaptureState) => void) | null,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/", useSearchParams: () => new URLSearchParams(),
}));
vi.mock("./controller", () => ({
  createCaptureController: () => ({
    start: mocks.start, stop: mocks.stop,
    getState: () => mocks.state,
    subscribe: (listener: typeof mocks.listener) => {
      mocks.listener = listener;
      return () => { mocks.listener = null; };
    },
    attachLifecycle: vi.fn(), updateRouteSignature: vi.fn(), destroy: vi.fn(),
    refreshPausedRecording: vi.fn().mockResolvedValue(null),
  }),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  setRecordingConsentNoticeOutcome: mocks.save,
  updateSettings: mocks.settings,
}));
vi.mock("@/components/ui/Modal", () => ({
  default: ({ title, children, footer, onClose }: {
    title: string; children: ReactNode; footer: ReactNode; onClose: () => void;
  }) => <div role="dialog" aria-label={title}>
    <button onClick={onClose}>Close</button>{children}{footer}
  </div>,
}));

function StartButton() {
  const { start } = useCapture();
  return <button onClick={() => void start().catch(() => {})}>Start</button>;
}

async function begin(outcome = "no_answer") {
  mocks.start.mockImplementation(async () => {
    mocks.state = { ...mocks.state, recordingId: "rec-1", runtimeActive: true, status: "recording" };
    mocks.listener?.(mocks.state);
    return { recordingId: "rec-1", resumed: false, consentNoticeOutcome: outcome };
  });
  render(<CaptureProvider><StartButton /><RecordingConsentNotice /></CaptureProvider>);
  await act(async () => { fireEvent.click(screen.getByText("Start")); });
}

async function click(label: string) {
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: label })); });
}

describe("recording consent", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mocks.state = { status: "idle", recordingId: null, runtimeActive: false } as CaptureState;
    mocks.save.mockResolvedValue({});
    mocks.settings.mockResolvedValue({ hide_recording_consent_notice: true });
    mocks.stop.mockResolvedValue({ ok: true });
  });
  afterEach(() => { vi.useRealTimers(); });

  it("saves agreement and dismisses without stopping", async () => {
    const updated = vi.fn();
    window.addEventListener("recording-updated", updated);
    await begin();
    await click("Everyone agreed");
    window.removeEventListener("recording-updated", updated);
    expect(mocks.save).toHaveBeenCalledWith("rec-1", "agreed");
    // The recording's page refreshes only for an update that names it.
    expect((updated.mock.calls[0][0] as CustomEvent).detail).toEqual({ id: "rec-1" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mocks.stop).not.toHaveBeenCalled();
  });

  it.each(["Continue", "Close"])("keeps the negative answer after %s", async (button) => {
    await begin();
    await click("Not everyone agreed");
    expect(screen.getByRole("dialog", { name: "Keep recording?" })).toBeInTheDocument();
    await click(button);
    expect(mocks.save).toHaveBeenCalledExactlyOnceWith("rec-1", "not_agreed");
    expect(mocks.stop).not.toHaveBeenCalled();
  });

  it("only an explicit Stop uses the normal capture stop path", async () => {
    await begin();
    await click("Not everyone agreed");
    expect(mocks.stop).not.toHaveBeenCalled();
    const updated = vi.fn();
    window.addEventListener("recording-updated", updated);
    await click("Stop the recording");
    act(() => vi.advanceTimersByTime(300));
    window.removeEventListener("recording-updated", updated);
    expect(mocks.stop).toHaveBeenCalledTimes(1);
    expect(mocks.save).toHaveBeenCalledWith("rec-1", "not_agreed");
    // As the meeting controls' Stop: the lists and the page hear the meeting ended.
    expect(updated).toHaveBeenCalledTimes(1);
  });

  it("dismisses an unanswered notice without changing its initial outcome", async () => {
    await begin();
    await click("Close");
    expect(mocks.save).not.toHaveBeenCalled();
    expect(mocks.stop).not.toHaveBeenCalled();
  });

  it("times out after 60 seconds despite live level updates, without stopping", async () => {
    await begin();
    for (let elapsed = 0; elapsed < 60; elapsed += 10) {
      act(() => {
        mocks.listener?.({ ...mocks.state });
        vi.advanceTimersByTime(10_000);
      });
    }
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mocks.save).not.toHaveBeenCalled();
    expect(mocks.stop).not.toHaveBeenCalled();
  });

  it("gives the second prompt its own timeout, which keeps recording", async () => {
    await begin();
    act(() => vi.advanceTimersByTime(50_000));
    await click("Not everyone agreed");
    act(() => vi.advanceTimersByTime(59_999));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mocks.stop).not.toHaveBeenCalled();
    expect(mocks.save).toHaveBeenCalledExactlyOnceWith("rec-1", "not_agreed");
  });

  it("saves suppression through the existing user settings API", async () => {
    await begin();
    await act(async () => fireEvent.click(screen.getByRole("checkbox")));
    expect(mocks.settings).toHaveBeenCalledWith({ hide_recording_consent_notice: true });
    await click("Everyone agreed");
    expect(mocks.save).toHaveBeenCalledWith("rec-1", "agreed");
  });

  it("shows no notice when the account suppressed it", async () => {
    await begin("not_shown");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mocks.save).not.toHaveBeenCalled();
  });

  it("does not show the notice in a watching tab with a live recording", () => {
    mocks.state = { ...mocks.state, status: "recording", recordingId: "rec-1" };
    render(<CaptureProvider><StartButton /><RecordingConsentNotice /></CaptureProvider>);
    act(() => mocks.listener?.({ ...mocks.state }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mocks.start).not.toHaveBeenCalled();
  });

  it("keeps the notice available to retry if saving the answer fails", async () => {
    mocks.save.mockRejectedValueOnce(new Error("offline"));
    await begin();
    await click("Not everyone agreed");
    expect(screen.getByRole("dialog", { name: "Recording consent" })).toBeInTheDocument();
    await click("Not everyone agreed");
    expect(screen.getByRole("dialog", { name: "Keep recording?" })).toBeInTheDocument();
    expect(mocks.stop).not.toHaveBeenCalled();
  });
});

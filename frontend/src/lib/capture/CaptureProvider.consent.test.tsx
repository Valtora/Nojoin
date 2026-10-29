import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RecordingConsentNotice from "@/components/RecordingConsentNotice";
import CaptureProcessingSettings from "@/components/settings/CaptureProcessingSettings";
import SettingsProvider from "@/components/settings/SettingsProvider";
import {
  installCaptureBrowser,
  uninstallCaptureBrowser,
  type FakeCaptureBrowser,
} from "@/test/fakeCaptureBrowser";

import { CaptureProvider, useCapture } from "./CaptureProvider";

/**
 * The routes a recording's start, answer and stop reach, answering as the
 * backend's tests show they do (test_consent_notice_postgres.py and
 * test_recording_pause_resume.py): Start answers "not_shown" once the user's
 * saved settings hide the notice, and "no_answer" otherwise.
 */
const server = vi.hoisted(() => ({
  settings: {} as Record<string, unknown>,
  outcomes: new Map<string, string>(),
  started: 0,
  saveFailures: 0,
}));

const api = vi.hoisted(() => ({
  initRecording: vi.fn(async () => {
    server.started += 1;
    const id = `rec-${server.started}`;
    const outcome = server.settings.hide_recording_consent_notice === true ? "not_shown" : "no_answer";
    server.outcomes.set(id, outcome);
    return { id, name: "Meeting", upload_token: null, capture_epoch: 0, consent_notice_outcome: outcome };
  }),
  setRecordingConsentNoticeOutcome: vi.fn(async (id: string, outcome: string) => {
    if (server.saveFailures > 0) {
      server.saveFailures -= 1;
      throw { isAxiosError: true, message: "Network Error" };
    }
    server.outcomes.set(id, outcome);
    return { id, consent_notice_outcome: outcome };
  }),
  finalizeRecordingCapture: vi.fn(async (id: string) => ({
    id,
    name: "Meeting",
    status: "QUEUED",
    client_status: null,
    consent_notice_outcome: server.outcomes.get(id) ?? null,
  })),
  getSettings: vi.fn(async () => ({ ...server.settings })),
  updateSettings: vi.fn(async (next: Record<string, unknown>) => {
    Object.assign(server.settings, next);
    return { ...server.settings };
  }),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/", useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  ...api,
  getUnfinishedCaptures: vi.fn(async () => []),
  reportRecordingCaptureSources: vi.fn(async () => undefined),
  uploadRecordingSegment: vi.fn(async () => ({ status: "ok", segment: 0 })),
  getUserMe: vi.fn(async () => ({
    id: 1, username: "mira", role: "user", is_superuser: false, force_password_change: false,
  })),
}));

/** The meeting controls, reduced to what these tests press and read. */
function Controls() {
  const { start, stop, status, elapsedSeconds } = useCapture();
  return <>
    <button onClick={() => void start().catch(() => {})}>Start</button>
    <button onClick={() => void stop().catch(() => {})}>Stop</button>
    <output aria-label="Capture status">{status}</output>
    <output aria-label="Elapsed">{elapsedSeconds}</output>
  </>;
}

let browser: FakeCaptureBrowser;

/** Lets the capture's own awaits and short timers run, as a browser would. */
const settle = () => act(() => vi.advanceTimersByTimeAsync(20));

const press = async (name: string) => {
  fireEvent.click(screen.getByRole("button", { name }));
  await settle();
};

const status = () => screen.getByRole("status", { name: "Capture status", hidden: true }).textContent;

const consentDialog = () => screen.queryByRole("dialog", { name: "Recording consent" });

/** One tab of the dashboard, with the settings page open below the controls when asked. */
const tab = (settingsPage = false) => <CaptureProvider>
  <Controls />
  <RecordingConsentNotice />
  {settingsPage && <SettingsProvider><CaptureProcessingSettings /></SettingsProvider>}
</CaptureProvider>;

async function startRecording() {
  const view = render(tab());
  await settle();
  await press("Start");
  expect(status()).toBe("recording");
  return view;
}

describe("recording consent notice in the tab that starts the recording", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    browser = installCaptureBrowser();
    server.settings = {};
    server.outcomes.clear();
    server.started = 0;
    server.saveFailures = 0;
  });
  afterEach(() => {
    vi.useRealTimers();
    uninstallCaptureBrowser();
  });

  it("opens on Start, and Everyone agreed saves the answer and closes it while recording continues", async () => {
    await startRecording();
    // The real capture path picked its sources before the notice opened.
    expect(browser.mediaDevices.getDisplayMedia).toHaveBeenCalledTimes(1);
    expect(consentDialog()).toBeInTheDocument();

    const updated = vi.fn();
    window.addEventListener("recording-updated", updated);
    await press("Everyone agreed");
    window.removeEventListener("recording-updated", updated);

    expect(server.outcomes.get("rec-1")).toBe("agreed");
    // The recording's page refreshes only for an update that names it.
    expect((updated.mock.calls[0][0] as CustomEvent).detail).toEqual({ id: "rec-1" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(status()).toBe("recording");
    expect(api.finalizeRecordingCapture).not.toHaveBeenCalled();
  });

  it.each([
    ["its Close button", () => fireEvent.click(screen.getByRole("button", { name: "Close" }))],
    ["Escape", () => fireEvent.keyDown(consentDialog()!, { key: "Escape" })],
  ])("closes an unanswered notice with %s, leaving the answer unset and recording on", async (_, dismiss) => {
    await startRecording();
    expect(consentDialog()).toBeInTheDocument();

    dismiss();
    await settle();

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.setRecordingConsentNoticeOutcome).not.toHaveBeenCalled();
    expect(server.outcomes.get("rec-1")).toBe("no_answer");
    expect(status()).toBe("recording");
  });

  it.each(["Continue", "Close"])("keeps the negative answer and the recording after %s", async (button) => {
    await startRecording();
    await press("Not everyone agreed");
    expect(screen.getByRole("dialog", { name: "Keep recording?" })).toBeInTheDocument();

    await press(button);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.setRecordingConsentNoticeOutcome).toHaveBeenCalledExactlyOnceWith("rec-1", "not_agreed");
    expect(server.outcomes.get("rec-1")).toBe("not_agreed");
    expect(status()).toBe("recording");
  });

  it("stops through the capture's own stop only when Stop the recording is pressed", async () => {
    await startRecording();
    await press("Not everyone agreed");
    expect(api.finalizeRecordingCapture).not.toHaveBeenCalled();
    const updated = vi.fn();
    window.addEventListener("recording-updated", updated);

    await press("Stop the recording");
    await act(() => vi.advanceTimersByTimeAsync(300));
    window.removeEventListener("recording-updated", updated);

    expect(api.finalizeRecordingCapture).toHaveBeenCalledTimes(1);
    expect(api.finalizeRecordingCapture.mock.calls[0][0]).toBe("rec-1");
    expect(browser.microphone()?.stop).toHaveBeenCalled();
    expect(status()).toBe("idle");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // As the meeting controls' Stop: the lists and the page hear the meeting ended.
    expect(updated).toHaveBeenCalledTimes(1);
    expect(server.outcomes.get("rec-1")).toBe("not_agreed");
  });

  it("closes once the recording stops itself because screen sharing ended", async () => {
    await startRecording();
    expect(consentDialog()).toBeInTheDocument();

    act(() => browser.endScreenShare());
    await settle();

    expect(api.finalizeRecordingCapture).toHaveBeenCalledTimes(1);
    expect(status()).toBe("idle");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("times out after 60 seconds while the recording's clock ticks, without stopping", async () => {
    await startRecording();
    await act(() => vi.advanceTimersByTimeAsync(59_000));
    // The capture re-rendered the tab every second meanwhile.
    expect(Number(screen.getByRole("status", { name: "Elapsed", hidden: true }).textContent)).toBeGreaterThanOrEqual(58);
    expect(consentDialog()).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(1_000));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.setRecordingConsentNoticeOutcome).not.toHaveBeenCalled();
    expect(status()).toBe("recording");
  });

  it("gives the second prompt its own timeout, which keeps recording", async () => {
    await startRecording();
    await act(() => vi.advanceTimersByTimeAsync(50_000));
    await press("Not everyone agreed");
    await act(() => vi.advanceTimersByTimeAsync(59_900));
    expect(screen.getByRole("dialog", { name: "Keep recording?" })).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(200));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(status()).toBe("recording");
    expect(api.setRecordingConsentNoticeOutcome).toHaveBeenCalledExactlyOnceWith("rec-1", "not_agreed");
  });

  it("keeps the notice open to retry when saving the answer fails", async () => {
    server.saveFailures = 1;
    await startRecording();

    await press("Not everyone agreed");
    expect(consentDialog()).toBeInTheDocument();
    expect(server.outcomes.get("rec-1")).toBe("no_answer");

    await press("Not everyone agreed");
    expect(screen.getByRole("dialog", { name: "Keep recording?" })).toBeInTheDocument();
    expect(server.outcomes.get("rec-1")).toBe("not_agreed");
    expect(status()).toBe("recording");
  });

  it("stays hidden on the next Start after Don't show this again, until Settings turns it back on", async () => {
    const view = await startRecording();
    fireEvent.click(screen.getByRole("checkbox", { name: "Don't show this again" }));
    await settle();
    expect(server.settings.hide_recording_consent_notice).toBe(true);
    expect(screen.getByRole("checkbox", { name: "Don't show this again" })).toBeChecked();
    await press("Everyone agreed");
    await press("Stop");

    await press("Start");
    expect(status()).toBe("recording");
    expect(server.outcomes.get("rec-2")).toBe("not_shown");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await press("Stop");

    // Settings, opened afresh, loads the saved preference.
    view.rerender(tab(true));
    await settle();
    const toggle = screen.getByRole("switch", { name: "Show the notice" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    fireEvent.click(toggle);
    await settle();
    expect(server.settings.hide_recording_consent_notice).toBe(false);
    view.rerender(tab());

    await press("Start");
    expect(status()).toBe("recording");
    expect(server.outcomes.get("rec-3")).toBe("no_answer");
    expect(consentDialog()).toBeInTheDocument();
  });
});

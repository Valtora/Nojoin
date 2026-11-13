import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNotificationStore } from "@/lib/notificationStore";
import { ClientStatus, RecordingStatus, type LiveAiNotice, type Recording } from "@/types";
import { liveAiNotification, useLiveAiNotices } from "./useLiveAiNotices";

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const retrying: LiveAiNotice = {
  id: "n-retry", purposes: ["meeting_edge"], provider: "Ollama", model: "fake-500",
  kind: "unavailable", action: "retrying", retry_at: "2026-11-13T10:00:30Z",
  fixable_by: "you", settings: null,
  message: "Meeting Edge: Ollama returned a server error for fake-500. Nojoin will try it again in 30 seconds.",
};
const stopped: LiveAiNotice = {
  id: "n-stop", purposes: ["meeting_edge"], provider: "Ollama", model: "fake-401",
  kind: "auth", action: "stopped", retry_at: null, fixable_by: "you", settings: "ai-providers",
  message: "Meeting Edge: Ollama refused the API key for fake-401. Nojoin has stopped using it until the AI settings change.",
};

const capture = (notices: LiveAiNotice[], overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1", created_at: "2026-11-13T10:00:00Z", updated_at: "2026-11-13T10:00:00Z",
  name: "Weekly sync", meeting_uid: "m-1", audio_path: "/a.wav", status: RecordingStatus.UPLOADING,
  client_status: ClientStatus.RECORDING, upload_progress: 0, processing_progress: 0,
  is_archived: false, is_deleted: false, live_ai_notices: notices, ...overrides,
});

const active = () => useNotificationStore.getState().activeNotifications;

describe("live AI notices", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    useNotificationStore.setState({ activeNotifications: [], history: [] });
    router.push.mockReset();
  });
  afterEach(cleanup);

  it("shows a retrying notice once, as a transient warning with no action", () => {
    const { rerender } = renderHook(({ recording }) => useLiveAiNotices(recording), {
      initialProps: { recording: capture([retrying]) },
    });
    rerender({ recording: capture([{ ...retrying, message: "Nojoin will try it again in 25 seconds." }]) });

    expect(active()).toHaveLength(1);
    expect(active()[0]).toMatchObject({
      id: "live-ai-n-retry", type: "warning", message: retrying.message, persistent: false,
    });
    expect(active()[0].actions).toBeUndefined();
  });

  it("keeps a stopped notice on screen and opens the Settings page that fixes it", () => {
    renderHook(() => useLiveAiNotices(capture([stopped])));

    const [toast] = active();
    expect(toast).toMatchObject({ type: "error", persistent: true, message: stopped.message });
    expect(toast.actions?.map((action) => action.label)).toEqual(["Open Settings"]);
    act(() => toast.actions?.[0].onClick());
    expect(router.push).toHaveBeenCalledWith("/settings/ai-providers");
  });

  it("offers no Settings page to someone who cannot fix it", () => {
    const notification = liveAiNotification(
      { ...stopped, fixable_by: "admin", settings: null }, router.push,
    );

    expect(notification.actions).toBeUndefined();
    expect(notification.persistent).toBe(true);
  });

  it("takes a notice's toast away once its provider answers, and does not bring it back", () => {
    const { rerender } = renderHook(({ recording }) => useLiveAiNotices(recording), {
      initialProps: { recording: capture([stopped]) },
    });
    expect(active()).toHaveLength(1);

    rerender({ recording: capture([]) });
    expect(active()).toHaveLength(0);

    rerender({ recording: capture([stopped]) });
    expect(active()).toHaveLength(0);
  });

  it("does not repeat a notice after the page reloads", () => {
    renderHook(() => useLiveAiNotices(capture([retrying])));
    cleanup();
    useNotificationStore.setState({ activeNotifications: [] });

    renderHook(() => useLiveAiNotices(capture([retrying])));

    expect(active()).toHaveLength(0);
  });

  it("shows nothing for a recording that is no longer being captured", () => {
    renderHook(() => useLiveAiNotices(capture([retrying], {
      status: RecordingStatus.PROCESSING, client_status: ClientStatus.IDLE,
    })));

    expect(active()).toHaveLength(0);
  });
});

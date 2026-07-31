import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Recording, RecordingStatus } from "@/types";

import { useDashboardRecordings } from "./useDashboardRecordings";

const getRecordings = vi.fn();

vi.mock("@/lib/api", () => ({
  getRecordings: (...args: unknown[]) => getRecordings(...args),
}));

const recording = (over: Partial<Recording> & { id: string; created_at: string }): Recording =>
  ({
    name: `Recording ${over.id}`,
    meeting_uid: `uid-${over.id}`,
    audio_path: "/tmp/audio.wav",
    status: RecordingStatus.PROCESSED,
    is_archived: false,
    is_deleted: false,
    updated_at: over.created_at,
    ...over,
  }) as Recording;

describe("useDashboardRecordings", () => {
  beforeEach(() => {
    getRecordings.mockReset();
  });

  it("returns the newest recordings first, capped at five", async () => {
    getRecordings.mockResolvedValue([
      recording({ id: "1", created_at: "2026-07-01T10:00:00Z" }),
      recording({ id: "2", created_at: "2026-07-06T10:00:00Z" }),
      recording({ id: "3", created_at: "2026-07-03T10:00:00Z" }),
      recording({ id: "4", created_at: "2026-07-05T10:00:00Z" }),
      recording({ id: "5", created_at: "2026-07-02T10:00:00Z" }),
      recording({ id: "6", created_at: "2026-07-04T10:00:00Z" }),
    ]);

    const { result } = renderHook(() => useDashboardRecordings());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.recent.map((r) => r.id)).toEqual(["2", "4", "6", "3", "5"]);
  });

  it("excludes archived and deleted recordings", async () => {
    getRecordings.mockResolvedValue([
      recording({ id: "1", created_at: "2026-07-01T10:00:00Z" }),
      recording({ id: "2", created_at: "2026-07-02T10:00:00Z", is_archived: true }),
      recording({ id: "3", created_at: "2026-07-03T10:00:00Z", is_deleted: true }),
    ]);

    const { result } = renderHook(() => useDashboardRecordings());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.recent.map((r) => r.id)).toEqual(["1"]);
  });

  it("treats uploading, queued and processing as in flight, and nothing else", async () => {
    getRecordings.mockResolvedValue([
      recording({ id: "up", created_at: "2026-07-05T10:00:00Z", status: RecordingStatus.UPLOADING }),
      recording({ id: "q", created_at: "2026-07-04T10:00:00Z", status: RecordingStatus.QUEUED }),
      recording({ id: "p", created_at: "2026-07-03T10:00:00Z", status: RecordingStatus.PROCESSING }),
      recording({ id: "done", created_at: "2026-07-02T10:00:00Z", status: RecordingStatus.PROCESSED }),
      recording({ id: "err", created_at: "2026-07-01T10:00:00Z", status: RecordingStatus.ERROR }),
    ]);

    const { result } = renderHook(() => useDashboardRecordings());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.processing.map((r) => r.id)).toEqual(["up", "q", "p"]);
  });

  it("refetches when a recording changes, so the in-flight list drains", async () => {
    getRecordings.mockResolvedValueOnce([
      recording({ id: "1", created_at: "2026-07-01T10:00:00Z", status: RecordingStatus.PROCESSING }),
    ]);

    const { result } = renderHook(() => useDashboardRecordings());
    await waitFor(() => expect(result.current.processing).toHaveLength(1));

    getRecordings.mockResolvedValueOnce([
      recording({ id: "1", created_at: "2026-07-01T10:00:00Z", status: RecordingStatus.PROCESSED }),
    ]);
    window.dispatchEvent(new Event("recording-updated"));

    await waitFor(() => expect(result.current.processing).toHaveLength(0));
    expect(result.current.recent).toHaveLength(1);
  });

  it("hides itself rather than throwing when the fetch fails", async () => {
    getRecordings.mockRejectedValue(new Error("offline"));

    const { result } = renderHook(() => useDashboardRecordings());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.recent).toEqual([]);
    expect(result.current.processing).toEqual([]);
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";

import { useBackupStore } from "./backupStore";

describe("backupStore", () => {
  beforeEach(() => {
    useBackupStore.setState({ taskId: null, startedAt: null });
    vi.useRealTimers();
  });

  it("stamps a start time when a task begins", () => {
    // The poller needs this to give up on a stale task. Celery reports PENDING for an
    // unknown id exactly as it does for a queued one, so a persisted id whose result has
    // expired would otherwise be polled forever with no terminal state to stop on.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));

    useBackupStore.getState().setTaskId("task-abc");

    expect(useBackupStore.getState().taskId).toBe("task-abc");
    expect(useBackupStore.getState().startedAt).toBe(
      new Date("2026-01-01T00:00:00Z").getTime(),
    );
  });

  it("clears the start time when the task is cleared", () => {
    useBackupStore.getState().setTaskId("task-abc");
    useBackupStore.getState().setTaskId(null);

    expect(useBackupStore.getState().taskId).toBeNull();
    expect(useBackupStore.getState().startedAt).toBeNull();
  });

  it("restamps when a new task replaces an old one", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    useBackupStore.getState().setTaskId("task-old");

    vi.setSystemTime(new Date("2026-01-01T05:00:00Z"));
    useBackupStore.getState().setTaskId("task-new");

    // The new task gets the full timeout window, not the remainder of the old one's.
    expect(useBackupStore.getState().startedAt).toBe(
      new Date("2026-01-01T05:00:00Z").getTime(),
    );
  });
});

describe("backupStore progress", () => {
  beforeEach(() => {
    useBackupStore.setState({ taskId: null, startedAt: null, progress: null });
  });

  it("holds the latest reported progress", () => {
    useBackupStore.getState().setTaskId("task-abc");
    useBackupStore.getState().setProgress({
      status: "Compressing audio (12 of 340)",
      stage: "Compressing audio",
      current: 12,
      total: 340,
    });

    expect(useBackupStore.getState().progress?.current).toBe(12);
    expect(useBackupStore.getState().progress?.total).toBe(340);
  });

  it("clears stale progress when a new task starts", () => {
    // Otherwise the panel would briefly show the previous export's position against the
    // new one, which reads as the bar jumping backwards.
    useBackupStore.getState().setProgress({
      status: "Compressing audio (300 of 340)",
      current: 300,
      total: 340,
    });

    useBackupStore.getState().setTaskId("task-new");

    expect(useBackupStore.getState().progress).toBeNull();
  });
});

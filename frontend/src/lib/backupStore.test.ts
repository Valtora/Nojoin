import { beforeEach, describe, expect, it } from "vitest";

import type { BackupExport } from "./api";
import { useBackupStore } from "./backupStore";

const ready: BackupExport = {
  export_id: "build-1",
  created_at: "2026-10-17T14:02:09+00:00",
  include_audio: false,
  archive_quality: "compressed",
  size_bytes: 10,
  warnings: {},
};

describe("backupStore", () => {
  beforeEach(() => {
    localStorage.clear();
    useBackupStore.setState({ taskId: null, progress: null, readyExport: null });
  });

  it("clears stale progress when a new task begins", () => {
    useBackupStore.getState().setProgress({ status: "Copying audio" });

    useBackupStore.getState().setTaskId("task-abc");

    expect(useBackupStore.getState().taskId).toBe("task-abc");
    expect(useBackupStore.getState().progress).toBeNull();
  });

  it("persists the task and the ready backup but not progress", () => {
    useBackupStore.getState().setTaskId("task-abc");
    useBackupStore.getState().setProgress({ status: "Copying audio" });
    useBackupStore.getState().setReadyExport(ready);

    const persisted = JSON.parse(localStorage.getItem("nojoin-backup-store") ?? "{}");

    expect(persisted.state).toEqual({ taskId: "task-abc", readyExport: ready });
  });
});

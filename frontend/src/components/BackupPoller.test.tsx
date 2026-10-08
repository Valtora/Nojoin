import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, waitFor } from "@/test/renderWithProviders";

const addNotification = vi.fn();
const getBackupStatus = vi.fn();
const downloadBackupFile = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

const backupState = {
  taskId: "task-1",
  startedAt: 0,
  setTaskId: vi.fn(),
  setProgress: vi.fn(),
};

vi.mock("@/lib/backupStore", () => ({
  useBackupStore: () => backupState,
}));

vi.mock("@/lib/api", () => ({
  getBackupStatus: (...args: unknown[]) => getBackupStatus(...args),
  downloadBackupFile: (...args: unknown[]) => downloadBackupFile(...args),
}));

import BackupPoller from "./BackupPoller";

const finishBackupWith = async (warnings: Record<string, number>) => {
  getBackupStatus.mockResolvedValue({ state: "SUCCESS", result: { warnings } });
  const { unmount } = renderWithProviders(<BackupPoller />);
  await waitFor(() => expect(downloadBackupFile).toHaveBeenCalledWith("task-1"));
  unmount();
};

const errorMessages = () =>
  addNotification.mock.calls
    .map(([notification]) => notification)
    .filter((notification) => notification.type === "error")
    .map((notification) => notification.message);

describe("BackupPoller warnings about audio left out of the archive", () => {
  beforeEach(() => {
    addNotification.mockReset();
    getBackupStatus.mockReset();
    downloadBackupFile.mockReset().mockResolvedValue(undefined);
  });

  it("says nothing more when every recording's audio was archived", async () => {
    await finishBackupWith({ recordings_without_audio: 0, recordings_audio_failed: 0 });

    expect(errorMessages()).toEqual([]);
  });

  it("reports one recording of each kind in the singular", async () => {
    await finishBackupWith({ recordings_without_audio: 1, recordings_audio_failed: 1 });

    expect(errorMessages()).toEqual([
      "1 recording had no audio file on disk and was backed up as metadata only.",
      "The audio of 1 recording could not be archived, so it was backed up as metadata only. Check the worker logs.",
    ]);
  });

  it("reports several recordings in the plural", async () => {
    await finishBackupWith({ recordings_without_audio: 3, recordings_audio_failed: 2 });

    expect(errorMessages()).toEqual([
      "3 recordings had no audio file on disk and were backed up as metadata only.",
      "The audio of 2 recordings could not be archived, so they were backed up as metadata only. Check the worker logs.",
    ]);
  });
});

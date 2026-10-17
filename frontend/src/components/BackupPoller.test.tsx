import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, waitFor } from "@/test/renderWithProviders";
import type { BackupExport, BackupStatus } from "@/lib/api";
import { useBackupStore } from "@/lib/backupStore";
import { useNotificationStore } from "@/lib/notificationStore";

const getBackupStatus = vi.fn();
const downloadBackupFile = vi.fn();

vi.mock("@/lib/api", () => ({
  getBackupStatus: (...args: unknown[]) => getBackupStatus(...args),
  downloadBackupFile: (...args: unknown[]) => downloadBackupFile(...args),
}));

import BackupPoller from "./BackupPoller";
import NotificationToast from "./NotificationToast";

const backup = (warnings: Record<string, number> = {}): BackupExport => ({
  export_id: "build-1",
  created_at: "2026-10-17T14:02:09+00:00",
  include_audio: true,
  archive_quality: "compressed",
  size_bytes: 1024,
  warnings,
});

const finished = (warnings: Record<string, number> = {}): BackupStatus => ({
  state: "SUCCESS",
  status: "Backup ready",
  export: backup(warnings),
});

const renderPoller = () =>
  renderWithProviders(
    <>
      <BackupPoller />
      <NotificationToast />
    </>,
  );

const active = () => useNotificationStore.getState().activeNotifications;

const messages = (type: string) =>
  active()
    .filter((notification) => notification.type === type)
    .map((notification) => notification.message);

describe("BackupPoller", () => {
  beforeEach(() => {
    getBackupStatus.mockReset();
    downloadBackupFile.mockReset();
    useNotificationStore.setState({ activeNotifications: [], history: [] });
    useBackupStore.setState({ taskId: "task-1", progress: null, readyExport: null });
  });

  it("offers the finished backup instead of downloading it unasked", async () => {
    getBackupStatus.mockResolvedValue(finished());
    renderPoller();

    await screen.findByRole("button", { name: "Download Now" });

    expect(downloadBackupFile).not.toHaveBeenCalled();
    expect(useBackupStore.getState().taskId).toBeNull();
    expect(useBackupStore.getState().readyExport?.export_id).toBe("build-1");
  });

  it("downloads from the notification and then forgets it", async () => {
    getBackupStatus.mockResolvedValue(finished());
    renderPoller();

    fireEvent.click(await screen.findByRole("button", { name: "Download Now" }));

    expect(downloadBackupFile).toHaveBeenCalledWith("build-1");
    expect(useBackupStore.getState().readyExport).toBeNull();
    expect(active()).toHaveLength(0);
  });

  it("brings the notification back after a reload until it is used", async () => {
    useBackupStore.setState({ taskId: null, readyExport: backup() });
    renderPoller();

    expect(
      await screen.findByRole("button", { name: "Download Now" }),
    ).toBeInTheDocument();
    expect(getBackupStatus).not.toHaveBeenCalled();
  });

  it("forgets the backup when the notification is closed", async () => {
    useBackupStore.setState({ taskId: null, readyExport: backup() });
    renderPoller();
    await screen.findByRole("button", { name: "Download Now" });

    active()[0].onClose?.();

    expect(useBackupStore.getState().readyExport).toBeNull();
  });

  it("publishes progress while the backup is being built", async () => {
    getBackupStatus.mockResolvedValue({
      state: "PROCESSING",
      status: "Compressing audio (3 of 9)",
      stage: "Compressing audio",
      current: 3,
      total: 9,
    });
    renderPoller();

    await waitFor(() =>
      expect(useBackupStore.getState().progress).toEqual({
        status: "Compressing audio (3 of 9)",
        stage: "Compressing audio",
        current: 3,
        total: 9,
      }),
    );
    expect(useBackupStore.getState().taskId).toBe("task-1");
  });

  it("reports a failed backup", async () => {
    getBackupStatus.mockResolvedValue({
      state: "FAILURE",
      status: "The backup stopped making progress and was abandoned.",
    });
    renderPoller();

    await waitFor(() =>
      expect(messages("error")).toEqual([
        "Backup creation failed: The backup stopped making progress and was abandoned.",
      ]),
    );
    expect(useBackupStore.getState().taskId).toBeNull();
  });

  it("says so when the requested backup has since been replaced", async () => {
    getBackupStatus.mockResolvedValue({
      state: "EXPIRED",
      status: "That backup has been replaced by a newer one or deleted.",
    });
    renderPoller();

    await waitFor(() =>
      expect(messages("info")).toEqual([
        "That backup has been replaced by a newer one or deleted.",
      ]),
    );
    expect(useBackupStore.getState().readyExport).toBeNull();
  });

  it("keeps polling through a network error", async () => {
    getBackupStatus.mockRejectedValue(new Error("offline"));
    vi.spyOn(console, "error").mockImplementation(() => {});
    renderPoller();

    await waitFor(() => expect(getBackupStatus).toHaveBeenCalled());
    expect(useBackupStore.getState().taskId).toBe("task-1");
  });
});

describe("BackupPoller warnings about audio left out of the archive", () => {
  beforeEach(() => {
    getBackupStatus.mockReset();
    downloadBackupFile.mockReset();
    useNotificationStore.setState({ activeNotifications: [], history: [] });
    useBackupStore.setState({ taskId: "task-1", progress: null, readyExport: null });
  });

  const finishWith = async (warnings: Record<string, number>) => {
    getBackupStatus.mockResolvedValue(finished(warnings));
    renderPoller();
    await screen.findByRole("button", { name: "Download Now" });
  };

  it("says nothing more when every recording's audio was archived", async () => {
    await finishWith({ recordings_without_audio: 0, recordings_audio_failed: 0 });

    expect(messages("error")).toEqual([]);
  });

  it("reports one recording of each kind in the singular", async () => {
    await finishWith({ recordings_without_audio: 1, recordings_audio_failed: 1 });

    expect(messages("error")).toEqual([
      "1 recording had no audio file on disk and was backed up as metadata only.",
      "The audio of 1 recording could not be archived, so it was backed up as metadata only. Check the worker logs.",
    ]);
  });

  it("reports several recordings in the plural", async () => {
    await finishWith({ recordings_without_audio: 3, recordings_audio_failed: 2 });

    expect(messages("error")).toEqual([
      "3 recordings had no audio file on disk and were backed up as metadata only.",
      "The audio of 2 recordings could not be archived, so they were backed up as metadata only. Check the worker logs.",
    ]);
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, waitFor } from "@/test/renderWithProviders";
import { useNotificationStore } from "@/lib/notificationStore";
import { useRestoreStore } from "@/lib/restoreStore";

const getRestoreStatus = vi.fn();
const routerPush = vi.fn();

vi.mock("@/lib/api", () => ({
  getRestoreStatus: (...args: unknown[]) => getRestoreStatus(...args),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));

import RestorePoller from "./RestorePoller";

const active = () => useNotificationStore.getState().activeNotifications;

describe("RestorePoller", () => {
  beforeEach(() => {
    getRestoreStatus.mockReset();
    routerPush.mockReset();
    useNotificationStore.setState({ activeNotifications: [], history: [] });
    useRestoreStore.setState({
      uploading: false,
      uploadPercent: 100,
      jobId: "restore-1",
      status: "",
      outcome: null,
    });
  });

  it("reports a clean restore wherever the person is", async () => {
    getRestoreStatus.mockResolvedValue({
      status: "completed",
      progress: "Done",
      error: null,
      warnings: { skipped: {} },
    });
    renderWithProviders(<RestorePoller />);

    await waitFor(() => expect(useRestoreStore.getState().jobId).toBeNull());
    expect(useRestoreStore.getState().outcome?.kind).toBe("completed");
    expect(active()[0]).toMatchObject({ type: "success", persistent: true });
  });

  it("keeps the skipped records for the settings panel", async () => {
    const skipped = { recordings: { unresolved_owner: 2 } };
    getRestoreStatus.mockResolvedValue({
      status: "completed_with_warnings",
      progress: "Done",
      error: null,
      warnings: { skipped },
    });
    renderWithProviders(<RestorePoller />);

    await waitFor(() =>
      expect(useRestoreStore.getState().outcome).toMatchObject({
        kind: "warnings",
        skipped,
      }),
    );
    expect(active()[0].message).toBe(
      "Backup restored, but 2 records could not be restored.",
    );

    active()[0].actions?.[0].onClick();
    expect(routerPush).toHaveBeenCalledWith("/settings/backup");
  });

  it("stops following a restore the server no longer knows", async () => {
    getRestoreStatus.mockResolvedValue({
      status: "unknown",
      progress: "Unknown",
      error: "The server is no longer tracking this restore.",
      warnings: null,
    });
    renderWithProviders(<RestorePoller />);

    await waitFor(() => expect(useRestoreStore.getState().jobId).toBeNull());
    expect(useRestoreStore.getState().outcome?.kind).toBe("unknown");
  });

  it("reports progress while the restore runs", async () => {
    getRestoreStatus.mockResolvedValue({
      status: "processing",
      progress: "Restoring database...",
      error: null,
      warnings: null,
    });
    renderWithProviders(<RestorePoller />);

    await waitFor(() =>
      expect(useRestoreStore.getState().status).toBe("Restoring database..."),
    );
    expect(useRestoreStore.getState().jobId).toBe("restore-1");
  });
});

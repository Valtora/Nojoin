import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ServiceStatusAlerts from "./ServiceStatusAlerts";
import type { Reachability } from "@/lib/connectivity/reducer";

const addNotification = vi.fn();
const removeActiveNotification = vi.fn();
const refreshHealth = vi.fn().mockResolvedValue(undefined);
const startPolling = vi.fn();
const stopPolling = vi.fn();

let connectivityStatus: Reachability = "online";

const serviceStatusState = {
  db: true,
  worker: true,
  deploymentWarnings: [] as Array<{
    code: string;
    key: string;
    title: string;
    message: string;
  }>,
  refreshHealth,
  startPolling,
  stopPolling,
};

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({
    addNotification,
    removeActiveNotification,
  }),
}));

vi.mock("@/lib/serviceStatusStore", () => ({
  useServiceStatusStore: () => serviceStatusState,
}));

vi.mock("@/lib/connectivity/monitor", () => ({
  useConnectivityStore: (selector: (state: { status: Reachability }) => unknown) =>
    selector({ status: connectivityStatus }),
  startConnectivityMonitor: vi.fn(),
  stopConnectivityMonitor: vi.fn(),
}));

describe("ServiceStatusAlerts", () => {
  beforeEach(() => {
    addNotification.mockReset();
    removeActiveNotification.mockReset();
    refreshHealth.mockClear();
    startPolling.mockClear();
    stopPolling.mockClear();
    addNotification.mockReturnValue("placeholder-toast-id");
    connectivityStatus = "online";
    serviceStatusState.db = true;
    serviceStatusState.worker = true;
    serviceStatusState.deploymentWarnings = [];
  });

  it("creates one persistent placeholder warning toast", async () => {
    serviceStatusState.deploymentWarnings = [
      {
        code: "placeholder_first_run_password",
        key: "FIRST_RUN_PASSWORD",
        title: "Placeholder bootstrap password configured",
        message: "Update it.",
      },
      {
        code: "placeholder_data_encryption_key",
        key: "DATA_ENCRYPTION_KEY",
        title: "Placeholder data encryption key configured",
        message: "Update it.",
      },
    ];

    render(<ServiceStatusAlerts />);

    await waitFor(() => {
      expect(addNotification).toHaveBeenCalledWith({
        type: "warning",
        message:
          "Security warning: Nojoin is using known placeholder secrets from the deployment templates (DATA_ENCRYPTION_KEY, FIRST_RUN_PASSWORD). Update .env and restart or redeploy Nojoin.",
        persistent: true,
      });
    });
  });

  it("removes the placeholder toast when warnings clear", async () => {
    serviceStatusState.deploymentWarnings = [
      {
        code: "placeholder_first_run_password",
        key: "FIRST_RUN_PASSWORD",
        title: "Placeholder bootstrap password configured",
        message: "Update it.",
      },
    ];

    const { rerender } = render(<ServiceStatusAlerts />);

    await waitFor(() => {
      expect(addNotification).toHaveBeenCalledTimes(1);
    });

    serviceStatusState.deploymentWarnings = [];
    rerender(<ServiceStatusAlerts />);

    await waitFor(() => {
      expect(removeActiveNotification).toHaveBeenCalledWith("placeholder-toast-id");
    });
  });

  it("raises the unreachable alert only in the unreachable state", async () => {
    connectivityStatus = "unreachable";

    render(<ServiceStatusAlerts />);

    await waitFor(() => {
      expect(addNotification).toHaveBeenCalledWith({
        type: "error",
        message: "Server Unreachable: Cannot connect to Nojoin Backend API.",
        persistent: true,
      });
    });
  });

  it("shows a distinct offline message when the browser is offline", async () => {
    connectivityStatus = "offline";

    render(<ServiceStatusAlerts />);

    await waitFor(() => {
      expect(addNotification).toHaveBeenCalledWith({
        type: "error",
        message: "You're offline. Check your network connection.",
        persistent: true,
      });
    });
  });

  it("does not raise a reachability alert while online or checking", async () => {
    connectivityStatus = "checking";

    render(<ServiceStatusAlerts />);

    await waitFor(() => {
      expect(startPolling).toHaveBeenCalled();
    });

    expect(addNotification).not.toHaveBeenCalledWith(
      expect.objectContaining({
        message: "Server Unreachable: Cannot connect to Nojoin Backend API.",
      }),
    );
  });
});

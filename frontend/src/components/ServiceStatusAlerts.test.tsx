import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ServiceStatusAlerts from "./ServiceStatusAlerts";

const addNotification = vi.fn();
const removeActiveNotification = vi.fn();
const checkBackend = vi.fn().mockResolvedValue(undefined);
const startPolling = vi.fn();
const stopPolling = vi.fn();

const serviceStatusState = {
  backend: true,
  db: true,
  worker: true,
  deploymentWarnings: [] as Array<{
    code: string;
    key: string;
    title: string;
    message: string;
  }>,
  backendFailCount: 0,
  checkBackend,
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

describe("ServiceStatusAlerts", () => {
  beforeEach(() => {
    addNotification.mockReset();
    removeActiveNotification.mockReset();
    checkBackend.mockClear();
    startPolling.mockClear();
    stopPolling.mockClear();
    addNotification.mockReturnValue("placeholder-toast-id");
    serviceStatusState.backend = true;
    serviceStatusState.db = true;
    serviceStatusState.worker = true;
    serviceStatusState.deploymentWarnings = [];
    serviceStatusState.backendFailCount = 0;
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
      expect(removeActiveNotification).toHaveBeenCalledWith(
        "placeholder-toast-id",
      );
    });
  });

  it("keeps backend-offline and placeholder-warning notifications separate", async () => {
    vi.useFakeTimers();
    addNotification
      .mockReturnValueOnce("backend-toast-id")
      .mockReturnValueOnce("placeholder-toast-id");
    serviceStatusState.backend = false;
    serviceStatusState.backendFailCount = 2;
    serviceStatusState.deploymentWarnings = [
      {
        code: "placeholder_first_run_password",
        key: "FIRST_RUN_PASSWORD",
        title: "Placeholder bootstrap password configured",
        message: "Update it.",
      },
    ];

    const { rerender } = render(<ServiceStatusAlerts />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5001);
    });

    serviceStatusState.backendFailCount = 3;
    rerender(<ServiceStatusAlerts />);

    expect(addNotification).toHaveBeenCalledWith({
      type: "error",
      message: "Server Unreachable: Cannot connect to Nojoin Backend API.",
      persistent: true,
    });

    expect(addNotification).not.toHaveBeenCalledWith({
      type: "warning",
      message: expect.stringContaining("placeholder secrets"),
      persistent: true,
    });

    vi.useRealTimers();
  });
});

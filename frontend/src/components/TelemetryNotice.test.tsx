import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TelemetryNotice from "./TelemetryNotice";

const addNotification = vi.fn();
const getTelemetryStatus = vi.fn().mockResolvedValue({});
const markTelemetryNoticeShown = vi.fn().mockResolvedValue({});
const updateTelemetryEnabled = vi.fn().mockResolvedValue({});

let telemetryNoticePending = false;

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: (selector: (state: unknown) => unknown) =>
    selector({ addNotification }),
}));

vi.mock("@/lib/serviceStatusStore", () => ({
  useServiceStatusStore: (selector: (state: unknown) => unknown) =>
    selector({ telemetryNoticePending }),
}));

vi.mock("@/lib/api", () => ({
  getTelemetryStatus: (...args: unknown[]) => getTelemetryStatus(...args),
  markTelemetryNoticeShown: (...args: unknown[]) =>
    markTelemetryNoticeShown(...args),
  updateTelemetryEnabled: (...args: unknown[]) => updateTelemetryEnabled(...args),
}));

describe("TelemetryNotice", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    telemetryNoticePending = false;
  });

  it("stays hidden and starts no clock when there is nothing to decide", () => {
    render(<TelemetryNotice />);

    expect(screen.queryByText(/share anonymous usage data/i)).toBeNull();
    expect(markTelemetryNoticeShown).not.toHaveBeenCalled();
  });

  it("starts the grace period only when the notice actually renders", async () => {
    telemetryNoticePending = true;

    render(<TelemetryNotice />);

    // The stamp is what makes an upgraded install eligible to send at all, so
    // it must happen on render rather than on any later user action.
    await waitFor(() => {
      expect(markTelemetryNoticeShown).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByText(/can now share anonymous usage data/i)).toBeVisible();
  });

  it("turns telemetry on when the admin keeps it", async () => {
    telemetryNoticePending = true;
    render(<TelemetryNotice />);

    fireEvent.click(screen.getByRole("button", { name: "Keep it on" }));

    await waitFor(() => {
      expect(updateTelemetryEnabled).toHaveBeenCalledWith(true);
    });
  });

  it("turns telemetry off when the admin declines", async () => {
    telemetryNoticePending = true;
    render(<TelemetryNotice />);

    fireEvent.click(screen.getByRole("button", { name: "Turn it off" }));

    await waitFor(() => {
      expect(updateTelemetryEnabled).toHaveBeenCalledWith(false);
    });
  });

  it("dismissing closes the banner without deciding either way", async () => {
    telemetryNoticePending = true;
    render(<TelemetryNotice />);

    fireEvent.click(screen.getByRole("button", { name: "Dismiss for now" }));

    await waitFor(() => {
      expect(screen.queryByText(/can now share anonymous usage data/i)).toBeNull();
    });
    // Dismissal is not a decision: the setting is untouched and the grace
    // clock, already started by the render, keeps running.
    expect(updateTelemetryEnabled).not.toHaveBeenCalled();
  });
});

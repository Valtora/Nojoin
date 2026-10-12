import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TelemetryStatus } from "@/types";

import TelemetrySection from "./TelemetrySection";

const addNotification = vi.fn();
const getTelemetryStatus = vi.fn();
const updateTelemetryEnabled = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: (selector: (state: unknown) => unknown) =>
    selector({ addNotification }),
}));

vi.mock("@/lib/api", () => ({
  getTelemetryStatus: (...args: unknown[]) => getTelemetryStatus(...args),
  updateTelemetryEnabled: (...args: unknown[]) => updateTelemetryEnabled(...args),
}));

const STATUS: TelemetryStatus = {
  enabled: true,
  managed_by_env: false,
  notice_acknowledged: true,
  notice_pending: false,
  notice_first_shown_at: null,
  consent_granted: true,
  install_id: "3f2a5c1e-9b4d-4a7f-8e1c-2d6b0a9f4e33",
  endpoint: "https://telemetry.nojoin.co.uk/v1/ping",
  last_sent_at: null,
  last_attempt_at: null,
  last_attempt_ok: null,
  last_attempt_detail: null,
  grace_period_days: 7,
  heartbeat_enabled: true,
  heartbeat_blocked_by: null,
  heartbeat_endpoint: "https://telemetry.nojoin.co.uk/v1/heartbeat",
  heartbeat_last_sent_at: null,
  heartbeat_last_attempt_at: null,
  heartbeat_last_attempt_ok: null,
  heartbeat_last_attempt_detail: null,
};

async function renderWith(overrides: Partial<TelemetryStatus>) {
  getTelemetryStatus.mockResolvedValue({ ...STATUS, ...overrides });
  render(<TelemetrySection />);
  await screen.findByText("Install heartbeat");
}

describe("TelemetrySection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the heartbeat as always on, with no switch of its own", async () => {
    await renderWith({});

    expect(screen.getByText("Always on")).toBeVisible();
    expect(screen.getByText(/only NOJOIN_TELEMETRY_ENABLED=false stops it/)).toBeVisible();
    // The one switch on the card is the diagnostics toggle.
    expect(screen.getAllByRole("switch")).toHaveLength(1);
    expect(screen.getByText(/telemetry\.nojoin\.co\.uk\/v1\/heartbeat/)).toBeVisible();
  });

  it("says when the environment has stopped the heartbeat", async () => {
    await renderWith({
      heartbeat_enabled: false,
      heartbeat_blocked_by: "environment",
      managed_by_env: true,
      enabled: false,
    });

    expect(screen.getByText("Off")).toBeVisible();
    expect(screen.getByText(/NOJOIN_TELEMETRY_ENABLED=false stops it\./)).toBeVisible();
  });

  it("says that an opt-out from before the heartbeat still stands", async () => {
    await renderWith({
      heartbeat_enabled: false,
      heartbeat_blocked_by: "earlier_opt_out",
      enabled: false,
    });

    expect(screen.getByText(/that choice still stands/)).toBeVisible();
  });

  it("reports a failed heartbeat separately from the diagnostics", async () => {
    await renderWith({
      heartbeat_last_attempt_ok: false,
      heartbeat_last_attempt_detail: "The collector could not be reached.",
      heartbeat_last_attempt_at: "2026-10-12T08:00:00+00:00",
    });

    expect(screen.getByText("The last heartbeat did not arrive")).toBeVisible();
    expect(screen.queryByText("The last diagnostics ping did not arrive")).toBeNull();
  });

  it("tells the admin the heartbeat continues when diagnostics are switched off", async () => {
    await renderWith({});
    updateTelemetryEnabled.mockResolvedValue({ ...STATUS, enabled: false });

    fireEvent.click(screen.getByRole("switch"));

    await waitFor(() => {
      expect(addNotification).toHaveBeenCalledWith({
        type: "success",
        message: "Usage diagnostics are off. The daily heartbeat continues.",
      });
    });
  });
});

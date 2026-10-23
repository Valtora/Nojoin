import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SystemTab from "./SystemTab";

vi.mock("@/lib/api", () => ({
  default: { get: vi.fn() },
  API_BASE_URL: "/api/v1",
  // Never settles: these tests are about the logs, not the health checks.
  getAdminHealth: () => new Promise(() => {}),
}));

const context = { isSuperuser: false };

vi.mock("./SettingsProvider", () => ({
  useSettingsContext: () => context,
}));

// A plain function, so `new WebSocket(url)` can construct it.
const openSocket = vi.fn(function (this: unknown, url: string) {
  void url;
  return { close: vi.fn() };
});

describe("SystemTab", () => {
  beforeEach(() => {
    openSocket.mockClear();
    vi.stubGlobal("WebSocket", openSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows any administrator the service health but not the logs", () => {
    // The log stream and download refuse every account but the superuser.
    context.isSuperuser = false;
    render(<SystemTab />);

    expect(screen.getByRole("heading", { name: "Service health" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Logs" })).not.toBeInTheDocument();
    expect(openSocket).not.toHaveBeenCalled();
  });

  it("streams the logs for the setup account", () => {
    context.isSuperuser = true;
    render(<SystemTab />);

    expect(screen.getByRole("log", { name: "Service logs" })).toBeInTheDocument();
    expect(openSocket).toHaveBeenCalledTimes(1);
    expect(String(openSocket.mock.calls[0])).toContain("/system/logs/live");
  });
});

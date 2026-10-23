import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SystemTab from "./SystemTab";

const apiGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: { get: (...args: unknown[]) => apiGet(...args) },
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
    vi.restoreAllMocks();
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

  it("downloads every service as one file and lets the page drop it", async () => {
    // The default, All services, used to fail: the API had no case for it.
    context.isSuperuser = true;
    apiGet.mockReset().mockResolvedValue({ data: new Blob(["log line"]) });
    window.URL.createObjectURL = vi.fn(() => "blob:logs");
    const revokeObjectURL = vi.fn();
    window.URL.revokeObjectURL = revokeObjectURL;
    const saved: (string | null)[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function (this: HTMLAnchorElement) {
        saved.push(this.getAttribute("download"));
      },
    );
    render(<SystemTab />);

    fireEvent.click(screen.getByRole("button", { name: "Download" }));

    await waitFor(() => expect(saved).toEqual(["nojoin-all_logs.txt"]));
    expect(apiGet).toHaveBeenCalledWith("/system/logs/download?container=all", {
      responseType: "blob",
    });
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:logs");
  });
});

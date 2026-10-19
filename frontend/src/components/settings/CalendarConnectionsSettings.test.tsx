import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import type { CalendarOverview } from "@/types";

const context = { isAdmin: false };
vi.mock("./SettingsProvider", () => ({
  useSettingsContext: () => context,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/settings/integrations",
  useSearchParams: () => new URLSearchParams(),
}));

const unconfigured: CalendarOverview = {
  providers: [
    { provider: "google", configured: false },
    { provider: "microsoft", configured: false },
  ] as CalendarOverview["providers"],
  connections: [],
};

vi.mock("@/lib/api", () => ({
  getCalendarOverview: vi.fn(async () => unconfigured),
  disconnectCalendarConnection: vi.fn(),
  startCalendarAuthorisation: vi.fn(),
  syncCalendarConnection: vi.fn(),
  updateCalendarColor: vi.fn(),
  updateCalendarSelection: vi.fn(),
}));

import CalendarConnectionsSettings from "./CalendarConnectionsSettings";

beforeEach(() => {
  context.isAdmin = false;
});

describe("CalendarConnectionsSettings with no providers set up", () => {
  it("points an administrator at the credentials", async () => {
    context.isAdmin = true;
    render(<CalendarConnectionsSettings />);

    const link = await screen.findByRole("link", { name: "Add app credentials" });
    expect(link.getAttribute("href")).toBe("#integrations-calendar-providers");
    expect(screen.queryByText(/Ask an administrator/)).toBeNull();
  });

  it("tells everyone else who can set it up", async () => {
    render(<CalendarConnectionsSettings />);

    expect(await screen.findByText(/Ask an administrator to add it/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Add app credentials" })).toBeNull();
  });
});

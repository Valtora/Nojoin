import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

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

import { getCalendarOverview, syncCalendarConnection } from "@/lib/api";
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

describe("CalendarConnectionsSettings with a connection", () => {
  const connection = {
    id: 3,
    provider: "google",
    email: "alice@example.com",
    sync_status: "success",
    selected_calendar_count: 0,
    push_active: false,
    calendars: [],
  } as unknown as CalendarOverview["connections"][number];

  // Chrome moves focus from a disabled button to the page body (#614). The
  // other actions on the connection are disabled outright: they never had it.
  it("keeps focus on Sync now while it syncs", async () => {
    vi.mocked(getCalendarOverview).mockResolvedValueOnce({
      ...unconfigured,
      connections: [connection],
    });
    let answer: (value: typeof connection) => void = () => undefined;
    vi.mocked(syncCalendarConnection).mockImplementationOnce(
      () => new Promise((resolve) => (answer = resolve)),
    );
    render(<CalendarConnectionsSettings />);
    const sync = await screen.findByRole("button", { name: "Sync now" });
    sync.focus();

    fireEvent.click(sync);

    expect(sync).toBeEnabled();
    expect(sync).toHaveAttribute("aria-busy", "true");
    expect(sync).toHaveFocus();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeDisabled();
    fireEvent.click(sync);
    expect(syncCalendarConnection).toHaveBeenCalledTimes(1);

    await act(async () => answer(connection));
    expect(sync).not.toHaveAttribute("aria-busy");
    expect(sync).toHaveFocus();
  });
});

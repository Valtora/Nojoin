import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

import type { CalendarOverview } from "@/types";

const context = { isAdmin: false };
vi.mock("./SettingsProvider", () => ({
  useSettingsContext: () => context,
}));

// The address the card renders at; a sign-in comes back with its outcome in the
// query string.
const navigation = { search: new URLSearchParams(), replace: vi.fn() };
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: navigation.replace, push: vi.fn() }),
  usePathname: () => "/settings/integrations",
  useSearchParams: () => navigation.search,
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
  navigation.search = new URLSearchParams();
  navigation.replace.mockClear();
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

describe("CalendarConnectionsSettings after a sign-in fails", () => {
  it("says why Google refused the calendars, with the Workspace hint", async () => {
    navigation.search = new URLSearchParams(
      "calendar=error&provider=google&reason=forbidden",
    );
    render(<CalendarConnectionsSettings />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not connect Google Calendar");
    expect(alert).toHaveTextContent(
      "Google refused Nojoin access to this account's calendars.",
    );
    expect(alert).toHaveTextContent(
      "an administrator may have turned Calendar off for you, or restricted third-party apps' access to the Calendar API, in the Google Admin console.",
    );
    expect(alert).toHaveTextContent("Reason given by Google: forbidden");
    expect(navigation.replace).toHaveBeenCalledWith("/settings/integrations", {
      scroll: false,
    });
  });

  it("says why Microsoft refused the calendars", async () => {
    navigation.search = new URLSearchParams(
      "calendar=error&provider=microsoft&reason=ErrorAccessDenied",
    );
    render(<CalendarConnectionsSettings />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not connect Outlook Calendar");
    expect(alert).toHaveTextContent(
      "Microsoft refused Nojoin access to this account's calendars.",
    );
    expect(alert).toHaveTextContent("Reason given by Microsoft: ErrorAccessDenied");
  });

  it("stays until dismissed", async () => {
    navigation.search = new URLSearchParams("calendar=error&provider=google");
    render(<CalendarConnectionsSettings />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong while connecting.");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows nothing after a successful sign-in", async () => {
    navigation.search = new URLSearchParams("calendar=success&provider=google");
    render(<CalendarConnectionsSettings />);

    await screen.findByText(/Ask an administrator to add it/);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

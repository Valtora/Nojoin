import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fireEvent,
  renderWithProviders,
  screen,
  within,
} from "@/test/renderWithProviders";
import {
  RecordingStatus,
  type CalendarDashboardEvent,
  type CalendarDashboardRecording,
  type CalendarDashboardSummary,
} from "@/types";

const getCalendarDashboardSummary = vi.fn();

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    ...props
  }: React.ComponentPropsWithoutRef<"a">) => (
    <a href={typeof href === "string" ? href : "#"} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api", () => ({
  getCalendarDashboardSummary: (...args: unknown[]) =>
    getCalendarDashboardSummary(...args),
}));

vi.mock("@/lib/timezone", async () => {
  const actual = await vi.importActual<typeof import("@/lib/timezone")>(
    "@/lib/timezone",
  );
  return {
    ...actual,
    getUserTimeZone: () => Promise.resolve("UTC"),
  };
});

import AgendaCard from "./AgendaCard";
import MonthGridCard from "./MonthGridCard";
import { useCalendarDashboard } from "./useCalendarDashboard";

/**
 * The dashboard's arrangement in miniature: one hook call feeding both modules.
 * Rendering them together is the point, because the split only holds if a
 * single fetch serves both and the grid can drive the agenda.
 */
function CalendarModules() {
  const calendar = useCalendarDashboard();

  return (
    <>
      <MonthGridCard calendar={calendar} />
      <AgendaCard calendar={calendar} />
    </>
  );
}

function makeEvent(
  overrides: Partial<CalendarDashboardEvent> = {},
): CalendarDashboardEvent {
  return {
    id: 1,
    title: "Standup",
    provider: "google",
    calendar_id: 10,
    calendar_name: "Work",
    calendar_colour: "blue",
    meeting_url_trusted: false,
    is_all_day: false,
    starts_at: "2026-06-15T09:00:00.000Z",
    ends_at: "2026-06-15T09:30:00.000Z",
    linked_recordings: [],
    ...overrides,
  };
}

function makeRecording(
  overrides: Partial<CalendarDashboardRecording> = {},
): CalendarDashboardRecording {
  return {
    id: 100,
    name: "Recorded sync",
    starts_at: "2026-06-15T11:00:00.000Z",
    ends_at: "2026-06-15T11:45:00.000Z",
    duration_seconds: 2700,
    status: RecordingStatus.PROCESSED,
    speaker_names: ["Alice", "Bob"],
    tags: [],
    ...overrides,
  };
}

function makeSummary(
  overrides: Partial<CalendarDashboardSummary> = {},
): CalendarDashboardSummary {
  return {
    month: "2026-06",
    timezone: "UTC",
    state: "ready",
    provider_configured: true,
    is_syncing: false,
    connection_count: 1,
    selected_calendar_count: 1,
    day_counts: [],
    agenda_items: [],
    recording_items: [],
    next_event: null,
    ...overrides,
  };
}

describe("calendar dashboard modules", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-15T10:00:00.000Z"));
    getCalendarDashboardSummary.mockResolvedValue(makeSummary());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("requests the viewed month summary once for both modules", async () => {
    renderWithProviders(<CalendarModules />);

    await vi.waitFor(() => {
      expect(getCalendarDashboardSummary).toHaveBeenCalledWith("2026-06", "UTC");
    });
    // Both modules are on screen, and between them they made one request.
    expect(screen.getByText("Calendar")).toBeInTheDocument();
    expect(screen.getByText("Agenda")).toBeInTheDocument();
    expect(getCalendarDashboardSummary).toHaveBeenCalledTimes(1);
  });

  it("renders the month grid header label", async () => {
    renderWithProviders(<CalendarModules />);

    await vi.waitFor(() => {
      expect(screen.getByText("June 2026")).toBeInTheDocument();
    });
    // Weekday header row is part of the month grid.
    expect(screen.getByText("Mon")).toBeInTheDocument();
    expect(screen.getByText("Sun")).toBeInTheDocument();
  });

  it("derives the next-event helper text from the summary", async () => {
    getCalendarDashboardSummary.mockResolvedValue(
      makeSummary({
        next_event: makeEvent({
          starts_at: "2026-06-15T11:00:00.000Z",
          ends_at: "2026-06-15T11:30:00.000Z",
        }),
      }),
    );

    renderWithProviders(<CalendarModules />);

    await vi.waitFor(() => {
      // now=10:00, event at 11:00 -> "Next event in 1hr 0min"
      expect(screen.getByText(/Next event in 1hr/)).toBeInTheDocument();
    });
  });

  it("opens on the selected day and returns to the month on request", async () => {
    renderWithProviders(<CalendarModules />);

    await vi.waitFor(() => {
      expect(screen.getByText("June 2026")).toBeInTheDocument();
    });

    // The agenda opens scoped to today rather than to the whole month.
    expect(screen.getByText(/Monday 15 June/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Whole month" }));
    expect(screen.queryByText(/Monday 15 June/)).not.toBeInTheDocument();

    // Picking a day in the grid is what scopes the agenda back to a day.
    fireEvent.click(screen.getByRole("button", { name: "16" }));
    expect(screen.getByText(/Tuesday 16 June/)).toBeInTheDocument();
  });

  it("shows upcoming items in the month agenda and reveals past items on demand", async () => {
    // now is 10:00; the event ended at 09:30 (past), the recording ends at
    // 11:45 (still upcoming).
    getCalendarDashboardSummary.mockResolvedValue(
      makeSummary({
        agenda_items: [makeEvent({ id: 1, title: "Planning meeting" })],
        recording_items: [makeRecording({ id: 100, name: "Recorded sync" })],
      }),
    );

    renderWithProviders(<CalendarModules />);

    await vi.waitFor(() => {
      expect(screen.getByText("June 2026")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Whole month" }));

    await vi.waitFor(() => {
      expect(screen.getByText("Recorded sync")).toBeInTheDocument();
    });
    expect(screen.queryByText("Planning meeting")).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Show 1 past event" }),
    );

    expect(screen.getByText("Planning meeting")).toBeInTheDocument();
    expect(screen.getByText("Recorded sync")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Hide past events" }),
    ).toBeInTheDocument();
  });

  it("links a recording card to its recording detail page", async () => {
    getCalendarDashboardSummary.mockResolvedValue(
      makeSummary({
        recording_items: [makeRecording({ id: 100, name: "Recorded sync" })],
      }),
    );

    renderWithProviders(<CalendarModules />);

    await vi.waitFor(() => {
      expect(screen.getByText("June 2026")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Whole month" }));

    const card = await vi.waitFor(() =>
      screen.getByText("Recorded sync").closest("a"),
    );
    expect(card).toHaveAttribute("href", "/recordings/100");
    expect(within(card!).getByText("Alice, Bob")).toBeInTheDocument();
  });

  it("renders a compact join label instead of the raw meeting URL in the agenda", async () => {
    getCalendarDashboardSummary.mockResolvedValue(
      makeSummary({
        agenda_items: [
          makeEvent({
            title: "Investors call",
            starts_at: "2026-06-16T16:00:00.000Z",
            ends_at: "2026-06-16T17:00:00.000Z",
            meeting_url:
              "https://us02web.zoom.us/w/89206805925?tk=G2SKabvvz99wb7RA7XM2kJvfx0",
            meeting_url_trusted: true,
            meeting_url_host: "us02web.zoom.us",
          }),
        ],
      }),
    );

    renderWithProviders(<CalendarModules />);

    await vi.waitFor(() => {
      expect(screen.getByText("June 2026")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Whole month" }));

    const joinLink = await vi.waitFor(() =>
      screen.getByRole("link", {
        name: "Join meeting (us02web.zoom.us)",
      }),
    );
    expect(joinLink).toHaveAttribute(
      "href",
      "https://us02web.zoom.us/w/89206805925?tk=G2SKabvvz99wb7RA7XM2kJvfx0",
    );
    expect(
      screen.queryByText(/us02web\.zoom\.us\/w\/89206805925/),
    ).not.toBeInTheDocument();
  });

  it("opens an event details popover when a timeline bubble is clicked", async () => {
    const location =
      "Huckletree Oxford Circus - West London Coworking & Office Space, 213 Oxford St, London W1D 2LG, UK";
    getCalendarDashboardSummary.mockResolvedValue(
      makeSummary({
        agenda_items: [
          makeEvent({
            title: "Workshop",
            starts_at: "2026-06-15T11:00:00.000Z",
            ends_at: "2026-06-15T12:00:00.000Z",
            location,
            meeting_url: "https://meet.google.com/abc-defg-hij",
            meeting_url_trusted: true,
            meeting_url_host: "meet.google.com",
          }),
        ],
      }),
    );

    renderWithProviders(<CalendarModules />);

    const bubbles = await vi.waitFor(() => {
      const found = screen.getAllByRole("button", {
        name: "View details for Workshop",
      });
      expect(found.length).toBeGreaterThan(0);
      return found;
    });

    expect(screen.queryByText(/Join meeting/)).not.toBeInTheDocument();

    const [bubble] = bubbles;
    fireEvent.click(bubble);

    const joinLink = screen.getByRole("link", {
      name: /Join meeting \(meet\.google\.com\)/,
    });
    expect(joinLink).toHaveAttribute(
      "href",
      "https://meet.google.com/abc-defg-hij",
    );
    // The popover shows the full, unclipped location.
    expect(screen.getAllByText(location).length).toBeGreaterThan(0);
  });

  it("explains an unconnected calendar rather than reporting an empty day", async () => {
    getCalendarDashboardSummary.mockResolvedValue(
      makeSummary({ state: "no_accounts" }),
    );

    renderWithProviders(<CalendarModules />);

    await vi.waitFor(() => {
      expect(
        screen.getByText("No calendar accounts connected."),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/Nothing on/)).not.toBeInTheDocument();
  });

  it("disables the Today button while viewing today", async () => {
    renderWithProviders(<CalendarModules />);

    await vi.waitFor(() => {
      expect(screen.getByText("June 2026")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: "Today" })).toBeDisabled();
  });
});

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

import DashboardUpcomingMeetingsCard from "./DashboardUpcomingMeetingsCard";

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

describe("DashboardUpcomingMeetingsCard", () => {
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

  it("requests the viewed month summary in the resolved time zone", async () => {
    renderWithProviders(<DashboardUpcomingMeetingsCard />);

    await vi.waitFor(() => {
      expect(getCalendarDashboardSummary).toHaveBeenCalledWith("2026-06", "UTC");
    });
    expect(screen.getByText("Calendar")).toBeInTheDocument();
  });

  it("renders the month grid header label", async () => {
    renderWithProviders(<DashboardUpcomingMeetingsCard />);

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

    renderWithProviders(<DashboardUpcomingMeetingsCard />);

    await vi.waitFor(() => {
      // now=10:00, event at 11:00 -> "Next event in 1hr 0min"
      expect(screen.getByText(/Next event in 1hr/)).toBeInTheDocument();
    });
  });

  it("shows recordings and events in the agenda view", async () => {
    getCalendarDashboardSummary.mockResolvedValue(
      makeSummary({
        agenda_items: [makeEvent({ id: 1, title: "Planning meeting" })],
        recording_items: [makeRecording({ id: 100, name: "Recorded sync" })],
      }),
    );

    renderWithProviders(<DashboardUpcomingMeetingsCard />);

    await vi.waitFor(() => {
      expect(screen.getByText("June 2026")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Agenda/ }));

    await vi.waitFor(() => {
      expect(screen.getByText("Planning meeting")).toBeInTheDocument();
    });
    expect(screen.getByText("Recorded sync")).toBeInTheDocument();
  });

  it("links a recording card to its recording detail page", async () => {
    getCalendarDashboardSummary.mockResolvedValue(
      makeSummary({
        recording_items: [makeRecording({ id: 100, name: "Recorded sync" })],
      }),
    );

    renderWithProviders(<DashboardUpcomingMeetingsCard />);

    await vi.waitFor(() => {
      expect(screen.getByText("June 2026")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Agenda/ }));

    const card = await vi.waitFor(() =>
      screen.getByText("Recorded sync").closest("a"),
    );
    expect(card).toHaveAttribute("href", "/recordings/100");
    expect(within(card!).getByText("Alice, Bob")).toBeInTheDocument();
  });

  it("disables the Today button while viewing today", async () => {
    renderWithProviders(<DashboardUpcomingMeetingsCard />);

    await vi.waitFor(() => {
      expect(screen.getByText("June 2026")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: "Today" })).toBeDisabled();
  });
});

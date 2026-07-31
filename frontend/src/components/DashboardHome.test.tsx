import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/renderWithProviders";
import { Recording, RecordingStatus } from "@/types";

const useDashboardRecordings = vi.fn();

// The modules themselves are covered by their own tests. What is under test
// here is the dashboard's arrangement: which modules render, and in what order
// they come out on a phone.
vi.mock("./Workspace", () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("./MeetingControls", () => ({ default: () => <div>Meet now</div> }));
vi.mock("./DashboardTasksPanel", () => ({ default: () => <div>Tasks</div> }));
vi.mock("./upcomingMeetings/MonthGridCard", () => ({
  default: () => <div>Month grid</div>,
}));
vi.mock("./upcomingMeetings/AgendaCard", () => ({
  default: () => <div>Agenda</div>,
}));
vi.mock("./upcomingMeetings/useCalendarDashboard", () => ({
  useCalendarDashboard: () => ({}),
}));
vi.mock("./dashboardRecordings/useDashboardRecordings", () => ({
  useDashboardRecordings: () => useDashboardRecordings(),
}));

import DashboardHome from "./DashboardHome";

const recording = (id: string, status = RecordingStatus.PROCESSED): Recording =>
  ({
    id,
    name: `Recording ${id}`,
    meeting_uid: `uid-${id}`,
    audio_path: "/tmp/audio.wav",
    status,
    created_at: "2026-07-01T10:00:00Z",
    updated_at: "2026-07-01T10:00:00Z",
    is_archived: false,
    is_deleted: false,
  }) as Recording;

const moduleIds = (container: HTMLElement): string[] =>
  Array.from(container.querySelectorAll("[id^='dashboard-']")).map(
    (element) => element.id,
  );

/** The phone sequence is set by `order-*`, not by document order. */
const phoneOrder = (container: HTMLElement): string[] =>
  Array.from(container.querySelectorAll("[id^='dashboard-']"))
    .map((element) => {
      const order = Array.from(element.classList).find((name) =>
        /^order-\d+$/.test(name),
      );
      return { id: element.id, order: Number(order?.split("-")[1] ?? 0) };
    })
    .sort((left, right) => left.order - right.order)
    .map((entry) => entry.id);

describe("DashboardHome", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows only the floor modules for an account with no recordings", () => {
    useDashboardRecordings.mockReturnValue({ recent: [], processing: [] });

    const { container } = renderWithProviders(<DashboardHome />);

    expect(moduleIds(container).sort()).toEqual([
      "dashboard-agenda",
      "dashboard-meeting-controls",
      "dashboard-task-cards",
      "dashboard-upcoming-meetings",
    ]);
  });

  it("adds the processing module only while something is in flight", () => {
    useDashboardRecordings.mockReturnValue({
      recent: [],
      processing: [recording("1", RecordingStatus.PROCESSING)],
    });

    const { container } = renderWithProviders(<DashboardHome />);

    expect(moduleIds(container)).toContain("dashboard-processing");
    expect(moduleIds(container)).not.toContain("dashboard-recent-recordings");
  });

  it("shows all six modules for an active account", () => {
    useDashboardRecordings.mockReturnValue({
      recent: [recording("1")],
      processing: [recording("2", RecordingStatus.PROCESSING)],
    });

    const { container } = renderWithProviders(<DashboardHome />);

    expect(moduleIds(container)).toHaveLength(6);
  });

  it("puts the month grid above the agenda on a desktop, but not on a phone", () => {
    useDashboardRecordings.mockReturnValue({ recent: [], processing: [] });

    const { container } = renderWithProviders(<DashboardHome />);
    const grid = container.querySelector("#dashboard-upcoming-meetings");
    const agenda = container.querySelector("#dashboard-agenda");

    // Two independent sequences over the same elements: the phone order runs
    // the agenda early and the month grid last, the desktop order pairs them
    // in the calendar column with the grid on top.
    expect(grid).toHaveClass("order-6", "@min-[54rem]:order-1");
    expect(agenda).toHaveClass("order-3", "@min-[54rem]:order-2");
  });

  it("puts the action first on a phone, and the month grid last", () => {
    useDashboardRecordings.mockReturnValue({
      recent: [recording("1")],
      processing: [recording("2", RecordingStatus.PROCESSING)],
    });

    const { container } = renderWithProviders(<DashboardHome />);

    expect(phoneOrder(container)).toEqual([
      "dashboard-meeting-controls",
      "dashboard-processing",
      "dashboard-agenda",
      "dashboard-task-cards",
      "dashboard-recent-recordings",
      "dashboard-upcoming-meetings",
    ]);
  });
});

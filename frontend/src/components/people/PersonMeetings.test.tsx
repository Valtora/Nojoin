import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getPersonMeetings } from "@/lib/api";
import type { PersonMeetingsRead } from "@/types";
import { renderWithFillContract as render } from "@/test/fillMarkerContracts";
import { PersonMeetings } from "./PersonMeetings";

vi.mock("@/lib/api", () => ({ getPersonMeetings: vi.fn() }));
vi.mock("@/lib/useUserTimeZone", () => ({ useUserTimeZone: () => "America/Los_Angeles" }));

const empty: PersonMeetingsRead = {
  meetings: [], meeting_count: 0, total_talk_time_seconds: 0,
  first_seen: null, last_seen: null, talk_time_by_month: [],
};

function history(): PersonMeetingsRead {
  return {
    meeting_count: 75,
    total_talk_time_seconds: 3661,
    first_seen: "2025-01-02T00:15:00",
    last_seen: "2026-10-01T00:15:00",
    talk_time_by_month: [
      { month: "2026-10", talk_time_seconds: 120 },
      { month: "2026-09", talk_time_seconds: 60 },
    ],
    meetings: Array.from({ length: 50 }, (_, index) => ({
      recording_id: `rec-${index}`, title: `Meeting ${index}`,
      date: "2026-10-01T00:15:00",
      duration_seconds: index === 0 ? null : index === 1 ? 0 : 125,
      talk_time_seconds: 61,
    })),
  };
}

describe("person meeting history", () => {
  beforeEach(() => { vi.mocked(getPersonMeetings).mockReset(); });

  it("keeps full-history figures separate from the latest 50 and converts seconds for display", async () => {
    vi.mocked(getPersonMeetings).mockResolvedValue(history());
    render(<PersonMeetings personId={7} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading meetings");
    const disclosure = await screen.findByText("View meeting history");
    expect(disclosure.closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("75")).toBeInTheDocument();
    expect(screen.getByText("1h 1m")).toBeInTheDocument();
    expect(screen.getByText("1 Jan 2025")).toBeInTheDocument();
    // Match the existing date-fns formatter's abbreviated month names.
    expect(screen.getByText("30 Sep 2026")).toBeInTheDocument();
    expect(screen.getByText("Meeting 0")).not.toBeVisible();
    fireEvent.click(disclosure);
    expect(disclosure.closest("details")).toHaveAttribute("open");
    expect(screen.getByRole("heading", { name: "Latest 50 meetings" })).toBeVisible();
    expect(screen.getAllByRole("link")).toHaveLength(50);
    const unknown = screen.getByText("Meeting 0").closest("a")!;
    expect(unknown).toHaveAttribute("href", "/recordings/rec-0");
    expect(unknown).toHaveTextContent("30 Sep 2026 · Duration: Unknown · Talk time: 1m 1s");
    expect(screen.getByText("Meeting 1").closest("a")).toHaveTextContent("Duration: 0s");
    expect(screen.getByText("Meeting 2").closest("a")).toHaveTextContent("Duration: 2m 5s");
    expect(screen.getByRole("heading", { name: "Talk time by month (UTC)" })).toBeInTheDocument();
    const months = within(screen.getByRole("list", { name: "Monthly talk time" })).getAllByRole("listitem");
    expect(months[0]).toHaveTextContent("Oct 2026");
    expect(months[0]).toHaveTextContent("2m 0s");
    expect(months[1]).toHaveTextContent("Sep 2026");
    expect(months[0].querySelector('[style]')).toHaveStyle({ width: "100%" });
    expect(months[1].querySelector('[style]')).toHaveStyle({ width: "50%" });
  });

  it("explains an empty history without inventing dates or zero-valued figures", async () => {
    vi.mocked(getPersonMeetings).mockResolvedValue(empty);
    render(<PersonMeetings personId={7} />);
    expect(await screen.findByText(/No linked meetings yet/)).toBeInTheDocument();
    expect(screen.queryByText("First seen")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText("View meeting history")).not.toBeInTheDocument();
  });

  it("retries a failed read without submitting an enclosing form", async () => {
    vi.mocked(getPersonMeetings).mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(empty);
    const submit = vi.fn();
    render(<form onSubmit={submit}><PersonMeetings personId={7} /></form>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load meeting history");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/No linked meetings yet/)).toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
    expect(getPersonMeetings).toHaveBeenCalledTimes(2);
  });

  it("does not show a late answer for a previously viewed person", async () => {
    let answer: (data: PersonMeetingsRead) => void = () => undefined;
    vi.mocked(getPersonMeetings).mockImplementationOnce(() => new Promise((resolve) => { answer = resolve; }))
      .mockResolvedValueOnce(empty);
    const view = render(<PersonMeetings personId={7} />);
    view.rerender(<PersonMeetings personId={8} />);
    await screen.findByText(/No linked meetings yet/);
    await act(async () => answer(history()));
    expect(screen.queryByText("Meeting 0")).not.toBeInTheDocument();
    expect(screen.getByText(/No linked meetings yet/)).toBeInTheDocument();
  });

  it("clears the previous person's figures while the next history loads", async () => {
    vi.mocked(getPersonMeetings).mockResolvedValueOnce(history())
      .mockImplementationOnce(() => new Promise(() => {}));
    const view = render(<PersonMeetings personId={7} />);
    await screen.findByText("Meeting 0");
    view.rerender(<PersonMeetings personId={8} />);
    expect(screen.queryByText("Meeting 0")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading meetings");
    await waitFor(() => expect(getPersonMeetings).toHaveBeenLastCalledWith(8));
  });
});

import { describe, expect, it } from "vitest";

import {
  RecordingStatus,
  type CalendarDashboardEvent,
  type CalendarDashboardRecording,
} from "@/types";

import {
  buildMonthAgendaItems,
  getTimelineMetadataRowCapacity,
  getUrlHost,
  isAgendaItemPast,
  splitMonthAgendaItems,
} from "./calendarUtils";

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
    id: "100",
    name: "Recorded sync",
    starts_at: "2026-06-15T08:00:00.000Z",
    ends_at: "2026-06-15T08:45:00.000Z",
    duration_seconds: 2700,
    status: RecordingStatus.PROCESSED,
    speaker_names: [],
    tags: [],
    ...overrides,
  };
}

const now = new Date("2026-06-15T10:00:00.000Z");

describe("isAgendaItemPast", () => {
  it("treats an ended timed event as past", () => {
    const [item] = buildMonthAgendaItems([makeEvent()], []);
    expect(isAgendaItemPast(item, now)).toBe(true);
  });

  it("treats a live event as upcoming until it ends", () => {
    const [item] = buildMonthAgendaItems(
      [
        makeEvent({
          starts_at: "2026-06-15T09:30:00.000Z",
          ends_at: "2026-06-15T10:30:00.000Z",
        }),
      ],
      [],
    );
    expect(isAgendaItemPast(item, now)).toBe(false);
  });

  it("keeps an all-day event current for its whole final day", () => {
    const [today] = buildMonthAgendaItems(
      [
        makeEvent({
          is_all_day: true,
          starts_at: null,
          ends_at: null,
          start_date: "2026-06-15",
          end_date: "2026-06-16",
        }),
      ],
      [],
    );
    const [yesterday] = buildMonthAgendaItems(
      [
        makeEvent({
          is_all_day: true,
          starts_at: null,
          ends_at: null,
          start_date: "2026-06-14",
          end_date: "2026-06-15",
        }),
      ],
      [],
    );

    expect(isAgendaItemPast(today, new Date("2026-06-15T23:00:00.000Z"))).toBe(
      false,
    );
    expect(isAgendaItemPast(yesterday, new Date("2026-06-16T01:00:00.000Z"))).toBe(
      true,
    );
  });

  it("classifies recordings by their end time", () => {
    const [pastRecording] = buildMonthAgendaItems([], [makeRecording()]);
    const [upcomingRecording] = buildMonthAgendaItems(
      [],
      [
        makeRecording({
          starts_at: "2026-06-15T09:45:00.000Z",
          ends_at: "2026-06-15T10:30:00.000Z",
        }),
      ],
    );

    expect(isAgendaItemPast(pastRecording, now)).toBe(true);
    expect(isAgendaItemPast(upcomingRecording, now)).toBe(false);
  });
});

describe("splitMonthAgendaItems", () => {
  it("partitions items while preserving chronological order", () => {
    const items = buildMonthAgendaItems(
      [
        makeEvent({ id: 1, title: "Past meeting" }),
        makeEvent({
          id: 2,
          title: "Future meeting",
          starts_at: "2026-06-20T09:00:00.000Z",
          ends_at: "2026-06-20T10:00:00.000Z",
        }),
      ],
      [makeRecording({ id: "100" })],
    );

    const { pastItems, upcomingItems } = splitMonthAgendaItems(items, now);

    expect(pastItems).toHaveLength(2);
    expect(upcomingItems).toHaveLength(1);
    expect(
      upcomingItems[0].kind === "event" && upcomingItems[0].event.title,
    ).toBe("Future meeting");
  });
});

describe("getUrlHost", () => {
  it("extracts the hostname without the www prefix", () => {
    expect(getUrlHost("https://www.zoom.us/j/123?pwd=abc")).toBe("zoom.us");
    expect(getUrlHost("https://meet.google.com/abc-defg-hij")).toBe(
      "meet.google.com",
    );
  });

  it("accepts bare hosts and rejects non-URLs", () => {
    expect(getUrlHost("meet.google.com")).toBe("meet.google.com");
    expect(getUrlHost("Room 4, Main Office")).toBeNull();
    expect(getUrlHost(null)).toBeNull();
  });
});

describe("getTimelineMetadataRowCapacity", () => {
  it("scales metadata rows with the bubble height", () => {
    expect(getTimelineMetadataRowCapacity(undefined)).toBe(2);
    expect(getTimelineMetadataRowCapacity(66)).toBe(0);
    expect(getTimelineMetadataRowCapacity(88)).toBe(1);
    expect(getTimelineMetadataRowCapacity(132)).toBe(2);
  });
});

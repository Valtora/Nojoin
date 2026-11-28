import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { AssistantChange } from "@/types";

vi.mock("@/lib/api", () => ({
  getAssistantChanges: vi.fn(),
  getCalendarOverview: vi.fn(async () => ({ providers: [], connections: [] })),
}));

import { getAssistantChanges } from "@/lib/api";
import AssistantChangesSettings from "./AssistantChangesSettings";

function change(id: number, overrides: Partial<AssistantChange> = {}): AssistantChange {
  return {
    id,
    created_at: "2026-11-28T10:00:00Z",
    tool: "update_calendar_event",
    action: "update",
    scope: "single",
    connection_id: 1,
    provider: "proton",
    calendar_name: "Personal",
    event_title: "Design review",
    event_start: "2026-12-01T14:00:00Z",
    outcome: "succeeded",
    client_name: "Claude",
    emailed: [],
    notifications_summary: "Nobody was told.",
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(getAssistantChanges).mockReset();
});

describe("AssistantChangesSettings", () => {
  it("lists what assistants changed and who they emailed, newest first", async () => {
    vi.mocked(getAssistantChanges).mockResolvedValue({
      items: [
        change(2, {
          action: "create",
          emailed: [{ email: "bob@example.org", kind: "invitation" }],
        }),
        change(1, { outcome: "refused", error_code: "not_organiser" }),
      ],
      total: 3,
      skip: 0,
      limit: 20,
    });
    render(<AssistantChangesSettings />);

    expect(await screen.findByText("Created: Design review")).toBeTruthy();
    expect(screen.getByText("Emailed bob@example.org")).toBeTruthy();
    expect(screen.getByText("Refused")).toBeTruthy();
    expect(screen.getByText("Reason: not organiser")).toBeTruthy();

    vi.mocked(getAssistantChanges).mockResolvedValueOnce({
      items: [change(0, { event_title: "Older" })],
      total: 3,
      skip: 2,
      limit: 20,
    });
    fireEvent.click(screen.getByRole("button", { name: "Show older changes" }));
    expect(await screen.findByText("Changed: Older")).toBeTruthy();
    await waitFor(() =>
      expect(getAssistantChanges).toHaveBeenLastCalledWith({ connectionId: null, skip: 2, limit: 20 }),
    );
  });

  it("says when there is nothing yet", async () => {
    vi.mocked(getAssistantChanges).mockResolvedValue({ items: [], total: 0, skip: 0, limit: 20 });
    render(<AssistantChangesSettings />);

    expect(await screen.findByText("No assistant has changed your calendars yet.")).toBeTruthy();
  });
});

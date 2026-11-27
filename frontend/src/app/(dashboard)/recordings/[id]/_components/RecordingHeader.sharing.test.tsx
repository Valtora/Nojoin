import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";

import RecordingHeader from "./RecordingHeader";
import { RecordingStatus, type Recording } from "@/types";

// A meeting someone shared with the caller: who can leave it, and what they
// see once the owner archived or binned it.

const api = vi.hoisted(() => ({
  leaveShare: vi.fn(),
  requestRestore: vi.fn(),
}));
const notify = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => api);
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: notify }),
}));
vi.mock("@/components/AudioPlayer", () => ({ default: () => null }));
vi.mock("@/components/RecordingTagEditor", () => ({ default: () => null }));
vi.mock("@/components/LinkedEventPanel", () => ({ default: () => null }));
vi.mock("@/components/ShareRecordingDialog", () => ({ default: () => null }));

const shared = (overrides: Partial<Recording> = {}): Recording =>
  ({
    id: "rec-1",
    name: "Weekly sync",
    status: RecordingStatus.PROCESSED,
    access: "edit",
    owner: { username: "olive", display_name: "Olive Owner" },
    can_leave: true,
    is_archived: false,
    is_deleted: false,
    tags: [],
    created_at: "2026-11-26T10:00:00",
    updated_at: "2026-11-26T10:00:00",
    ...overrides,
  }) as Recording;

const header = (recording: Recording) => {
  const props: ComponentProps<typeof RecordingHeader> = {
    recording,
    playerSlot: <span />,
    isMobile: false,
    isEditingTitle: false,
    titleValue: recording.name,
    isMobileHeaderActionsOpen: false,
    currentTime: 0,
    audioRef: { current: null },
    onRefresh: vi.fn(),
    setTitleValue: vi.fn(),
    setIsEditingTitle: vi.fn(),
    setIsMobileHeaderActionsOpen: vi.fn(),
    onBack: vi.fn(),
    onTitleSubmit: vi.fn(),
    onTimeUpdate: vi.fn(),
    onPlay: vi.fn(),
    onPause: vi.fn(),
  };
  return render(<RecordingHeader {...props} />);
};

describe("RecordingHeader on a meeting shared with the caller", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("offers Leave only for a share by name, not one through a group", () => {
    const view = header(shared({ can_leave: true }));
    expect(screen.getByRole("button", { name: "Leave" })).toBeTruthy();
    view.unmount();

    header(shared({ can_leave: false }));
    expect(screen.queryByRole("button", { name: "Leave" })).toBeNull();
  });

  it("says an archived meeting is read-only and asks the owner to unarchive it", async () => {
    api.requestRestore.mockResolvedValue({ task_id: 7, already_asked: false });
    header(shared({ access: "view", is_archived: true }));

    expect(screen.getByText(/Olive Owner archived this meeting, so you can only read it/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Ask to unarchive it" }));

    await waitFor(() => expect(api.requestRestore).toHaveBeenCalledWith("rec-1"));
    expect(notify).toHaveBeenCalledWith(
      expect.objectContaining({ message: "Asked Olive Owner. It is in their tasks now." }),
    );
  });

  it("asks to restore a binned meeting", () => {
    header(shared({ access: "view", is_deleted: true }));
    expect(screen.getByRole("button", { name: "Ask to restore it" })).toBeTruthy();
  });

  it("shows no notice on a meeting that is not put away", () => {
    header(shared());
    expect(screen.queryByText(/so you can only read it/)).toBeNull();
  });
});

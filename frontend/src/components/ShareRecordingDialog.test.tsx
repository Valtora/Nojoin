import { afterEach, describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, screen, waitFor } from "@/test/renderWithProviders";
import type { RecordingId } from "@/types";

import ShareRecordingDialog from "./ShareRecordingDialog";

const api = vi.hoisted(() => ({
  listShares: vi.fn(),
  getUserDirectory: vi.fn(),
  getGroupDirectory: vi.fn(),
  upsertShare: vi.fn(),
  deleteShare: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

const RECORDING = "rec-1" as RecordingId;

function open() {
  return renderWithProviders(
    <ShareRecordingDialog
      recordingId={RECORDING}
      recordingName="Weekly sync"
      open
      onClose={() => undefined}
    />,
  );
}

afterEach(() => vi.clearAllMocks());

describe("ShareRecordingDialog", () => {
  it("lists who has access and offers only people and groups not yet added", async () => {
    api.listShares.mockResolvedValue([
      { id: 1, user: { id: 2, username: "rein" }, group: null, level: "view" },
    ]);
    api.getUserDirectory.mockResolvedValue([
      { id: 2, username: "rein" },
      { id: 3, username: "harm", display_name: "Harm" },
    ]);
    api.getGroupDirectory.mockResolvedValue([{ name: "nolai", member_count: 4 }]);
    open();

    expect(await screen.findByText("rein")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Share with Harm" })).toBeTruthy();
    expect(screen.getByText("Group · 4 members")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Share with rein" })).toBeNull();
  });

  it("narrows the list to what the search matches", async () => {
    api.listShares.mockResolvedValue([]);
    api.getUserDirectory.mockResolvedValue([
      { id: 3, username: "harm", display_name: "Harm" },
      { id: 4, username: "bernard", display_name: "Bernard" },
    ]);
    api.getGroupDirectory.mockResolvedValue([{ name: "nolai", member_count: 4 }]);
    open();

    await screen.findByText("Only you.");
    fireEvent.change(screen.getByLabelText("Search people and groups"), {
      target: { value: "ber" },
    });

    expect(screen.getByRole("button", { name: "Share with Bernard" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Share with Harm" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Share with nolai" })).toBeNull();
  });

  it("says so when there is nobody else to share with", async () => {
    api.listShares.mockResolvedValue([]);
    api.getUserDirectory.mockResolvedValue([]);
    api.getGroupDirectory.mockResolvedValue([]);
    open();

    expect(await screen.findByText(/Nobody else has an account here yet/)).toBeTruthy();
    expect(screen.queryByLabelText("Search people and groups")).toBeNull();
  });

  it("shares with a group at the chosen level", async () => {
    api.listShares.mockResolvedValue([]);
    api.getUserDirectory.mockResolvedValue([]);
    api.getGroupDirectory.mockResolvedValue([{ name: "nolai", member_count: 4 }]);
    api.upsertShare.mockResolvedValue([
      { id: 5, user: null, group: "nolai", level: "view" },
    ]);
    open();

    await screen.findByText("Only you.");
    fireEvent.change(screen.getByLabelText("Access for people you add"), {
      target: { value: "view" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Share with nolai" }));

    await waitFor(() =>
      expect(api.upsertShare).toHaveBeenCalledWith(RECORDING, { group: "nolai" }, "view"),
    );
    expect(await screen.findByText("Everyone in nolai")).toBeTruthy();
  });

  it("removes a share", async () => {
    api.listShares.mockResolvedValue([
      { id: 1, user: { id: 2, username: "rein" }, group: null, level: "edit" },
    ]);
    api.getUserDirectory.mockResolvedValue([]);
    api.getGroupDirectory.mockResolvedValue([]);
    api.deleteShare.mockResolvedValue([]);
    open();

    fireEvent.click(await screen.findByRole("button", { name: "Remove rein" }));
    await waitFor(() => expect(api.deleteShare).toHaveBeenCalledWith(RECORDING, 1));
    expect(await screen.findByText("Only you.")).toBeTruthy();
  });
});

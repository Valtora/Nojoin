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
    const options = screen.getAllByRole("option").map((option) => option.textContent);
    expect(options).toContain("Harm (harm)");
    expect(options).toContain("nolai (4 members)");
    expect(options).not.toContain("rein");
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
    fireEvent.change(screen.getByLabelText("Add people or a group"), {
      target: { value: "group:nolai" },
    });
    fireEvent.change(screen.getByLabelText("Access"), { target: { value: "view" } });
    fireEvent.click(screen.getByRole("button", { name: "Share" }));

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

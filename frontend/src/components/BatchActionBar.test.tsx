import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MemoryNoteStore,
  NOTE_RECORD_VERSION,
  setNoteStoreForTests,
} from "@/lib/notes/noteStore";
import { noteSignedIn, resetSessionForTests } from "@/lib/session";

import BatchActionBar from "./BatchActionBar";

const batchPermanentlyDeleteRecordings = vi.fn();

vi.mock("@/lib/api", () => ({
  batchArchiveRecordings: vi.fn(),
  batchRestoreRecordings: vi.fn(),
  batchSoftDeleteRecordings: vi.fn(),
  batchPermanentlyDeleteRecordings: (...args: unknown[]) =>
    batchPermanentlyDeleteRecordings(...args),
  batchAddTagToRecordings: vi.fn(),
  batchRemoveTagFromRecordings: vi.fn(),
}));

vi.mock("@/lib/store", () => ({
  useNavigationStore: () => ({
    selectedRecordingIds: ["rec-1", "rec-2"],
    clearSelection: vi.fn(),
    currentView: "deleted",
  }),
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

const kept = (recordingId: string) => ({
  version: NOTE_RECORD_VERSION,
  account: 1,
  recordingId,
  notes: [],
  draft: { text: "Half a thought", mode: "typing", typingStartMs: null },
  discards: [],
});

describe("the batch bar in the bin", () => {
  afterEach(() => {
    setNoteStoreForTests(null);
    resetSessionForTests();
  });

  it("forgets the notes kept in the browser for the recordings it deletes for good", async () => {
    const store = new MemoryNoteStore();
    setNoteStoreForTests(store);
    noteSignedIn({ username: "alice", force_password_change: false, id: 1 });
    for (const id of ["rec-1", "rec-2", "rec-3"]) await store.put(kept(id));
    batchPermanentlyDeleteRecordings.mockResolvedValue(undefined);
    render(<BatchActionBar onActionComplete={vi.fn()} />);

    fireEvent.click(screen.getByTitle("Permanently Delete"));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(async () => expect(await store.recordingsOf(1)).toEqual(["rec-3"]));
    expect(batchPermanentlyDeleteRecordings).toHaveBeenCalledWith(["rec-1", "rec-2"]);
  });
});

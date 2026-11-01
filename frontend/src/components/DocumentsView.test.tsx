import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { Document } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  getDocuments: vi.fn(),
  reparseDocument: vi.fn(),
  addNotification: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getDocuments: mocks.getDocuments,
}));
vi.mock("@/lib/api/documents", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/documents")>()),
  reparseDocument: mocks.reparseDocument,
}));
vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: mocks.addNotification }),
}));
vi.mock("./DocumentUploadModal", () => ({ default: () => null }));

import DocumentsView from "./DocumentsView";

const failed: Document = {
  id: 7, recording_id: "rec-1", title: "Agenda.pdf", file_path: "agenda.pdf",
  file_type: "application/pdf", status: "ERROR", parse_mode: "STRUCTURAL",
  pages_parsed: 0, created_at: "2026-10-31T09:00:00", updated_at: "2026-10-31T09:00:00",
  error_message: "The file is damaged.",
};
const failureNotices = () =>
  mocks.addNotification.mock.calls.filter(([notice]) => notice.message === "Agenda.pdf: The file is damaged.");

/** Opens the Documents view as each visit to the recording does, then leaves. */
const visit = async () => {
  const view = render(<DocumentsView recordingId="rec-1" />);
  await screen.findByText("Agenda.pdf");
  view.unmount();
};

describe("DocumentsView failure notices", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    mocks.getDocuments.mockResolvedValue([failed]);
  });

  it("announces a document's failure once, not on every visit to its recording", async () => {
    await visit();
    await visit();
    await visit();

    expect(failureNotices()).toHaveLength(1);
  });

  it("announces a failure again after Parse again fails too", async () => {
    mocks.reparseDocument.mockResolvedValue({ ...failed, status: "PENDING", error_message: undefined });
    const view = render(<DocumentsView recordingId="rec-1" />);
    await screen.findByText("Agenda.pdf");
    expect(failureNotices()).toHaveLength(1);

    fireEvent.click(screen.getByTitle("Parse again with visual analysis"));
    // Announced once the failure has been forgotten.
    await waitFor(() =>
      expect(mocks.addNotification).toHaveBeenCalledWith(expect.objectContaining({ type: "success" })),
    );
    view.unmount();
    await visit();

    expect(failureNotices()).toHaveLength(2);
  });
});

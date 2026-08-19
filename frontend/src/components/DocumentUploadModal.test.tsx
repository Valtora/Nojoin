import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fireEvent,
  renderWithProviders,
  screen,
  waitFor,
} from "@/test/renderWithProviders";

const addNotification = vi.fn();
const uploadDocument = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

vi.mock("@/lib/api", () => ({
  uploadDocument: (...args: unknown[]) => uploadDocument(...args),
}));

import DocumentUploadModal from "./DocumentUploadModal";

const makeFile = (name: string, bytes = 1024) => {
  const file = new File(["x"], name, { type: "application/octet-stream" });
  Object.defineProperty(file, "size", { value: bytes });
  return file;
};

const renderModal = (onSuccess = vi.fn()) => {
  renderWithProviders(
    <DocumentUploadModal
      isOpen
      onClose={vi.fn()}
      onSuccess={onSuccess}
      recordingId="rec_1"
    />,
  );
  return { onSuccess };
};

const selectFiles = (files: File[]) => {
  const input = screen.getByTestId("document-file-input");
  fireEvent.change(input, { target: { files } });
};

afterEach(() => {
  vi.clearAllMocks();
});

describe("DocumentUploadModal", () => {
  it("queues several files from one selection", () => {
    renderModal();
    selectFiles([makeFile("deck.pdf"), makeFile("notes.docx")]);

    expect(screen.getAllByTestId("document-upload-row")).toHaveLength(2);
    expect(screen.getByText("deck.pdf")).toBeInTheDocument();
    expect(screen.getByText("notes.docx")).toBeInTheDocument();
  });

  it("accepts a second selection without dropping the first", () => {
    renderModal();
    selectFiles([makeFile("deck.pdf")]);
    selectFiles([makeFile("notes.docx")]);

    expect(screen.getAllByTestId("document-upload-row")).toHaveLength(2);
  });

  it("rejects an unsupported file and keeps the supported ones", () => {
    renderModal();
    selectFiles([makeFile("deck.pdf"), makeFile("archive.zip")]);

    expect(screen.getAllByTestId("document-upload-row")).toHaveLength(1);
    expect(addNotification).toHaveBeenCalledWith(
      expect.objectContaining({ type: "error" }),
    );
  });

  it("uploads each file with its own visual-analysis choice", async () => {
    uploadDocument.mockResolvedValue({ id: 1 });
    const { onSuccess } = renderModal();
    selectFiles([makeFile("deck.pdf"), makeFile("plain.txt")]);

    // Visual analysis off for the second file only.
    fireEvent.click(
      screen.getByLabelText("Analyse plain.txt visually with AI"),
    );
    fireEvent.click(screen.getByRole("button", { name: /upload 2 documents/i }));

    await waitFor(() => expect(uploadDocument).toHaveBeenCalledTimes(2));
    expect(uploadDocument.mock.calls[0][2]).toEqual({ deepParse: true });
    expect(uploadDocument.mock.calls[1][2]).toEqual({ deepParse: false });
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(2));
  });

  it("cannot turn visual analysis off for an image, which has no text layer", () => {
    renderModal();
    selectFiles([makeFile("slide.png")]);

    const checkbox = screen.getByLabelText(
      "Analyse slide.png visually with AI",
    ) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    expect(checkbox.disabled).toBe(true);
  });

  it("turns visual analysis off for every file that allows it", () => {
    renderModal();
    selectFiles([makeFile("deck.pdf"), makeFile("plain.txt"), makeFile("slide.png")]);

    fireEvent.click(screen.getByRole("button", { name: /turn off for all/i }));

    expect(
      (screen.getByLabelText("Analyse deck.pdf visually with AI") as HTMLInputElement)
        .checked,
    ).toBe(false);
    expect(
      (screen.getByLabelText("Analyse plain.txt visually with AI") as HTMLInputElement)
        .checked,
    ).toBe(false);
    expect(
      (screen.getByLabelText("Analyse slide.png visually with AI") as HTMLInputElement)
        .checked,
    ).toBe(true);
  });

  it("retries only what failed, so a success is never uploaded twice", async () => {
    uploadDocument
      .mockResolvedValueOnce({ id: 1 })
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce({ id: 2 });
    renderModal();
    selectFiles([makeFile("deck.pdf"), makeFile("notes.docx")]);

    fireEvent.click(screen.getByRole("button", { name: /upload 2 documents/i }));
    await waitFor(() => expect(uploadDocument).toHaveBeenCalledTimes(2));

    const retry = await screen.findByRole("button", { name: /retry 1 document/i });
    fireEvent.click(retry);

    await waitFor(() => expect(uploadDocument).toHaveBeenCalledTimes(3));
    expect(uploadDocument.mock.calls[2][1].name).toBe("notes.docx");
  });
});

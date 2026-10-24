import { describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";

import ExportModal from "./ExportModal";

const renderModal = ({
  hasNotes = true,
  hasAnalytics = true,
  hasAudio = true,
}: { hasNotes?: boolean; hasAnalytics?: boolean; hasAudio?: boolean } = {}) => {
  const onExport = vi.fn();
  const onClose = vi.fn();
  renderWithProviders(
    <ExportModal
      isOpen
      onClose={onClose}
      onExport={onExport}
      hasNotes={hasNotes}
      hasAnalytics={hasAnalytics}
      hasAudio={hasAudio}
    />,
  );
  return { onExport, onClose };
};

const section = (name: string) => screen.getByRole("checkbox", { name });
const exportButton = () => screen.getByRole("button", { name: "Export" });
const audioButton = () => screen.getByRole("button", { name: "Export audio" });

describe("ExportModal sections", () => {
  it("starts from the notes as a PDF", () => {
    const { onExport } = renderModal();

    fireEvent.click(exportButton());

    expect(onExport).toHaveBeenCalledWith({
      kind: "document",
      sections: ["notes"],
      format: "pdf",
      includeNotes: true,
    });
  });

  it("starts from the transcript when there are no notes", () => {
    const { onExport } = renderModal({ hasNotes: false });

    expect(section("Meeting notes")).toBeDisabled();
    fireEvent.click(exportButton());

    expect(onExport).toHaveBeenCalledWith(
      expect.objectContaining({ sections: ["transcript"] }),
    );
  });

  it("exports any combination, including analytics, in the chosen format", () => {
    const { onExport } = renderModal();

    fireEvent.click(section("Analytics"));
    fireEvent.click(section("Transcript"));
    fireEvent.click(screen.getByRole("radio", { name: "Word (.docx)" }));
    fireEvent.click(exportButton());

    expect(onExport).toHaveBeenCalledWith({
      kind: "document",
      sections: ["notes", "analytics", "transcript"],
      format: "docx",
      includeNotes: true,
    });
  });

  it("puts the reader's notes in the transcript unless unticked, which needs the transcript", () => {
    const { onExport } = renderModal();
    const myNotes = section("My notes in the transcript");

    expect(myNotes).toBeDisabled();
    fireEvent.click(section("Transcript"));
    expect(myNotes).toBeEnabled();
    expect(myNotes).toBeChecked();
    fireEvent.click(myNotes);
    expect(myNotes).not.toBeChecked();
    fireEvent.click(exportButton());

    expect(onExport).toHaveBeenCalledWith(
      expect.objectContaining({ sections: ["notes", "transcript"], includeNotes: false }),
    );
  });

  it("puts the notes tick back on each time it opens", () => {
    const onExport = vi.fn();
    const modal = (isOpen: boolean) => (
      <ExportModal
        isOpen={isOpen}
        onClose={vi.fn()}
        onExport={onExport}
        hasNotes={false}
        hasAnalytics
        hasAudio
      />
    );
    const { rerender } = renderWithProviders(modal(true));
    fireEvent.click(section("My notes in the transcript"));
    expect(section("My notes in the transcript")).not.toBeChecked();

    rerender(modal(false));
    rerender(modal(true));

    expect(section("My notes in the transcript")).toBeChecked();
  });

  it("cannot export nothing", () => {
    const { onExport } = renderModal();

    fireEvent.click(section("Meeting notes"));

    expect(exportButton()).toBeDisabled();
    fireEvent.click(exportButton());
    expect(onExport).not.toHaveBeenCalled();
  });

  it("offers analytics only once the meeting has been processed", () => {
    renderModal({ hasAnalytics: false });

    expect(section("Analytics")).toBeDisabled();
    expect(
      screen.getByText("Available once the meeting has finished processing"),
    ).toBeInTheDocument();
  });
});

describe("ExportModal audio", () => {
  it("cannot export audio that is not available", () => {
    const { onExport } = renderModal({ hasAudio: false });

    expect(audioButton()).toBeDisabled();
    expect(
      screen.getByText("This recording's audio is not available"),
    ).toBeInTheDocument();

    fireEvent.click(audioButton());
    expect(onExport).not.toHaveBeenCalled();
  });

  it("exports the audio when it is there", () => {
    const { onExport, onClose } = renderModal();

    fireEvent.click(audioButton());

    expect(onExport).toHaveBeenCalledWith({ kind: "audio" });
    expect(onClose).toHaveBeenCalled();
  });
});

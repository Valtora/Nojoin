import { describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";

import ExportModal from "./ExportModal";

const renderModal = (hasAudio: boolean) => {
  const onExport = vi.fn();
  renderWithProviders(
    <ExportModal
      isOpen
      onClose={vi.fn()}
      onExport={onExport}
      hasNotes
      hasAudio={hasAudio}
    />,
  );
  return { onExport };
};

const audioOption = () => screen.getByRole("radio", { name: /Audio File/ });
const exportButton = () => screen.getByRole("button", { name: "Export" });

describe("ExportModal audio option", () => {
  it("cannot export audio that is not available", () => {
    const { onExport } = renderModal(false);

    expect(audioOption()).toBeDisabled();
    expect(
      screen.getByText("This recording's audio is not available"),
    ).toBeInTheDocument();

    fireEvent.click(audioOption());
    fireEvent.click(exportButton());

    expect(onExport).toHaveBeenCalledWith("transcript", "txt");
  });

  it("exports the audio when it is there", () => {
    const { onExport } = renderModal(true);

    fireEvent.click(audioOption());
    fireEvent.click(exportButton());

    expect(onExport).toHaveBeenCalledWith("audio", "txt");
  });
});

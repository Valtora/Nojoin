import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Capture } from "@/lib/api";

const reparseDocument = vi.fn(async () => ({}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  reparseDocument: (...args: unknown[]) => reparseDocument(...(args as [])),
  downloadDocument: vi.fn(),
}));
vi.mock("@/lib/notifyRequestError", () => ({ notifyRequestError: vi.fn() }));

const { default: CaptureViewer } = await import("./CaptureViewer");

const capture = (extra: Partial<Capture> = {}): Capture =>
  ({
    id: 4,
    recording_id: "rec-1",
    kind: "capture",
    capture_ms: 65_000,
    use_in_notes: false,
    title: "Capture at 01:05",
    file_type: "image/png",
    status: "READY",
    created_at: "2026-10-24T09:00:00",
    ...extra,
  }) as Capture;

describe("a capture at full size", () => {
  it("shows the image, and turns its notes switch", () => {
    const onSetUseInNotes = vi.fn();
    render(<CaptureViewer capture={capture()} onClose={vi.fn()} onSetUseInNotes={onSetUseInNotes} />);

    expect(screen.getByRole("img", { name: "The capture at 01:05" })).toHaveAttribute(
      "src",
      expect.stringContaining("/documents/4/download"),
    );
    const toggle = screen.getByRole("switch", { name: "Use in notes" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    fireEvent.click(toggle);

    expect(onSetUseInNotes).toHaveBeenCalledWith(capture(), true);
  });

  it("asks before it deletes, and closes once it has", async () => {
    const onDelete = vi.fn(async () => true);
    const onClose = vi.fn();
    render(
      <CaptureViewer capture={capture()} onClose={onClose} onSetUseInNotes={vi.fn()} onDelete={onDelete} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onDelete).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onDelete).toHaveBeenCalledWith(capture());
  });

  it("plays from the capture's time", () => {
    const onPlay = vi.fn();
    render(
      <CaptureViewer capture={capture()} onClose={vi.fn()} onSetUseInNotes={vi.fn()} onPlay={onPlay} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Play from 01:05" }));

    expect(onPlay).toHaveBeenCalledWith(65);
  });

  it("offers to read again one that could not be read", async () => {
    const onChanged = vi.fn();
    render(
      <CaptureViewer
        capture={capture({ status: "ERROR", error_message: "No vision model." })}
        onClose={vi.fn()}
        onSetUseInNotes={vi.fn()}
        onChanged={onChanged}
      />,
    );

    expect(screen.getByText("Couldn't be read: No vision model.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(reparseDocument).toHaveBeenCalledWith(4, { deepParse: true });
  });
});

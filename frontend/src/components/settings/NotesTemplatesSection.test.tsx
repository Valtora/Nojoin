import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { fillMarkerViolations } from "@/test/fillMarkerContracts";

const listNotesTemplates = vi.fn();

vi.mock("@/lib/api", () => ({
  listNotesTemplates: () => listNotesTemplates(),
  createNotesTemplate: vi.fn(),
  updateNotesTemplate: vi.fn(),
  deleteNotesTemplate: vi.fn(),
  copyNotesTemplate: vi.fn(),
  resetNotesTemplate: vi.fn(),
  previewNotesTemplate: vi.fn(),
  generateNotesTemplate: vi.fn(),
}));

import NotesTemplatesSection from "./NotesTemplatesSection";

const template = (isInstallDefault: boolean) => ({
  id: 7,
  name: "Board pack",
  description: "",
  sections: "## Summary",
  scope: "install",
  user_id: null,
  builtin_version: null,
  is_editable: true,
  is_stale: false,
  is_install_default: isInstallDefault,
  is_user_default: false,
});

const listResponse = (isInstallDefault: boolean) => ({
  templates: [template(isInstallDefault)],
  builtin: { name: "Nojoin default", description: "", sections: "## Summary" },
  limits: { max_sections_length: 8000, max_description_length: 200 },
});

describe("NotesTemplatesSection install default", () => {
  beforeEach(() => {
    listNotesTemplates.mockReset();
  });

  it("keeps the built-in fill in agreement after loading and changing the selection", async () => {
    listNotesTemplates.mockResolvedValue(listResponse(false));
    const onPersist = vi.fn();
    const view = (notes_template_id: number | null) => (
      <NotesTemplatesSection settings={{ notes_template_id }} onPersist={onPersist} />
    );
    const { baseElement, rerender } = render(view(null));
    await screen.findByText("Board pack");
    const builtin = screen.getByRole("button", { name: /Nojoin default/ });
    expect(builtin).toHaveClass("bg-action-tint");
    expect(fillMarkerViolations(baseElement)).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: /Board pack.*Shared/ }));
    expect(onPersist).toHaveBeenLastCalledWith({ notes_template_id: 7 });
    rerender(view(7));
    expect(builtin).not.toHaveClass("bg-action-tint");
    expect(fillMarkerViolations(baseElement)).toEqual([]);

    fireEvent.click(builtin);
    expect(onPersist).toHaveBeenLastCalledWith({ notes_template_id: null });
    rerender(view(null));
    expect(builtin).toHaveClass("bg-action-tint");
    expect(fillMarkerViolations(baseElement)).toEqual([]);
  });

  // Issue #149: the badge is server state, so without a refetch a successful
  // save left the row untouched and the link read as dead.
  it("shows the badge once the save has landed", async () => {
    listNotesTemplates
      .mockResolvedValueOnce(listResponse(false))
      .mockResolvedValue(listResponse(true));
    const onPersist = vi.fn().mockResolvedValue(undefined);

    render(
      <NotesTemplatesSection
        settings={{ install_notes_template_id: null }}
        onPersist={onPersist}
        isAdmin
      />,
    );

    fireEvent.click(await screen.findByText("Make the shared default"));

    await waitFor(() =>
      expect(screen.getByText("Stop using as the shared default")).toBeTruthy(),
    );
    expect(screen.getByText("Shared default")).toBeTruthy();
    expect(onPersist).toHaveBeenCalledWith(
      expect.objectContaining({ install_notes_template_id: 7 }),
    );
    expect(listNotesTemplates).toHaveBeenCalledTimes(2);
  });

  it("clears the install default and refetches", async () => {
    listNotesTemplates
      .mockResolvedValueOnce(listResponse(true))
      .mockResolvedValue(listResponse(false));
    const onPersist = vi.fn().mockResolvedValue(undefined);

    render(
      <NotesTemplatesSection
        settings={{ install_notes_template_id: 7 }}
        onPersist={onPersist}
        isAdmin
      />,
    );

    fireEvent.click(await screen.findByText("Stop using as the shared default"));

    await waitFor(() =>
      expect(screen.getByText("Make the shared default")).toBeTruthy(),
    );
    expect(onPersist).toHaveBeenCalledWith(
      expect.objectContaining({ install_notes_template_id: null }),
    );
  });

  // A rejected save must not leave the row claiming a default that the server
  // never accepted.
  it("keeps showing the server's answer when the save fails", async () => {
    listNotesTemplates.mockResolvedValue(listResponse(false));
    const onPersist = vi.fn().mockRejectedValue(new Error("read-only config"));

    render(
      <NotesTemplatesSection
        settings={{ install_notes_template_id: null }}
        onPersist={onPersist}
        isAdmin
      />,
    );

    fireEvent.click(await screen.findByText("Make the shared default"));

    await waitFor(() => expect(onPersist).toHaveBeenCalled());
    expect(screen.getByText("Make the shared default")).toBeTruthy();
    expect(screen.queryByText("Install default")).toBeNull();
  });

  it("does not offer the install default to a non-admin", async () => {
    listNotesTemplates.mockResolvedValue(listResponse(false));

    render(
      <NotesTemplatesSection
        settings={{ install_notes_template_id: null }}
        onPersist={vi.fn()}
      />,
    );

    await screen.findByText("Board pack");
    expect(screen.queryByText("Make the shared default")).toBeNull();
  });
});

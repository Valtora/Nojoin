import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import NotesTemplatePicker from "./NotesTemplatePicker";

const listNotesTemplates = vi.fn();

vi.mock("@/lib/api", () => ({
  listNotesTemplates: (...args: unknown[]) => listNotesTemplates(...args),
}));

const TEMPLATES = {
  templates: [
    {
      id: 7,
      name: "Interview notes",
      description: "Questions, observations and follow-ups.",
      sections: "## Questions",
      scope: "personal" as const,
      user_id: 1,
      builtin_version: null,
      is_editable: true,
      is_stale: false,
      is_install_default: false,
      is_user_default: false,
    },
  ],
  builtin: {
    name: "Nojoin default",
    description: "Summary, decisions, action items, detailed notes.",
    sections: "## Summary",
    version: 1,
  },
  limits: {
    max_sections_length: 8000,
    max_description_length: 200,
    max_glossary_length: 8000,
    max_templates_per_scope: 50,
  },
  is_admin: false,
};

describe("NotesTemplatePicker", () => {
  beforeEach(() => {
    listNotesTemplates.mockReset();
    listNotesTemplates.mockResolvedValue(TEMPLATES);
  });

  it("does not fetch structures until it is opened", () => {
    render(<NotesTemplatePicker onSelect={vi.fn()} />);

    // Most people never change structure per meeting, so the recording page must
    // not pay for this request on every visit.
    expect(listNotesTemplates).not.toHaveBeenCalled();
  });

  it("passes null for the built-in structure and an id for a template", async () => {
    const onSelect = vi.fn();
    render(<NotesTemplatePicker onSelect={onSelect} />);

    fireEvent.click(screen.getByLabelText("Choose notes structure"));
    await waitFor(() =>
      expect(screen.getByText("Interview notes")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByText("Nojoin default"));
    expect(onSelect).toHaveBeenCalledWith(null);

    fireEvent.click(screen.getByLabelText("Choose notes structure"));
    fireEvent.click(await screen.findByText("Interview notes"));
    expect(onSelect).toHaveBeenLastCalledWith(7);
  });

  it("stays usable when the structures cannot be loaded", async () => {
    listNotesTemplates.mockRejectedValue(new Error("offline"));
    const onSelect = vi.fn();
    render(<NotesTemplatePicker onSelect={onSelect} />);

    fireEvent.click(screen.getByLabelText("Choose notes structure"));
    await waitFor(() => expect(listNotesTemplates).toHaveBeenCalled());

    // The built-in entry is rendered locally, so a failed request never blocks
    // regenerating with the default structure.
    fireEvent.click(screen.getByText("Nojoin default"));
    expect(onSelect).toHaveBeenCalledWith(null);
  });
});

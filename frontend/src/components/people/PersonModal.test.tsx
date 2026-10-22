import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PersonModal } from "./PersonModal";
import { httpError } from "@/test/httpErrors";
import type { GlobalSpeaker } from "@/types";

const addNotification = vi.fn();
const updateGlobalSpeaker = vi.fn();
const deleteGlobalSpeakerEmbedding = vi.fn();
const createPeopleTag = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: Object.assign(() => ({ addNotification }), {
    getState: () => ({ addNotification }),
  }),
}));

vi.mock("@/lib/api", () => ({
  getPeopleTags: vi.fn().mockResolvedValue([]),
  createPeopleTag: (...a: unknown[]) => createPeopleTag(...a),
  getGlobalSpeakers: vi.fn().mockResolvedValue([]),
  mergeSpeakers: vi.fn(),
  deleteGlobalSpeakerEmbedding: (...a: unknown[]) =>
    deleteGlobalSpeakerEmbedding(...a),
  updateGlobalSpeaker: (...a: unknown[]) => updateGlobalSpeaker(...a),
}));

// "Link to different speaker" is what Create Voiceprint calls a force link.
const LOCKED_LINE =
  "Locked: only Link to different speaker, recalibrating or splitting changes it.";

const person = (
  id: number,
  name: string,
  voiceprint: boolean,
  locked: boolean,
): GlobalSpeaker =>
  ({
    id,
    name,
    has_voiceprint: voiceprint,
    is_voiceprint_locked: locked,
  }) as GlobalSpeaker;

const dana = (voiceprint: boolean, locked: boolean) =>
  person(7, "Dana", voiceprint, locked);

const modal = (
  shown: GlobalSpeaker,
  { onSave = vi.fn(), onVoiceprintLockChanged = vi.fn() } = {},
) => (
  <PersonModal
    person={shown}
    isOpen
    onClose={vi.fn()}
    onSave={onSave}
    onVoiceprintLockChanged={onVoiceprintLockChanged}
  />
);

/** The next lock request waits until the test answers it. */
const answerLater = () => {
  let answer: (value: GlobalSpeaker) => void = () => undefined;
  updateGlobalSpeaker.mockImplementationOnce(
    () =>
      new Promise<GlobalSpeaker>((resolve) => {
        answer = resolve;
      }),
  );
  return (value: GlobalSpeaker) => act(async () => answer(value));
};

describe("PersonModal voiceprint lock", () => {
  beforeEach(() => {
    addNotification.mockReset();
    deleteGlobalSpeakerEmbedding.mockReset();
    updateGlobalSpeaker
      .mockReset()
      .mockImplementation(async (id: number, data: Partial<GlobalSpeaker>) => ({
        ...dana(true, false),
        id,
        ...data,
      }));
  });

  it.each([
    { locked: false, button: "Lock voiceprint", after: "Unlock voiceprint" },
    { locked: true, button: "Unlock voiceprint", after: "Lock voiceprint" },
  ])(
    "$button saves only the lock and keeps the voiceprint",
    async ({ locked, button, after }) => {
      const onVoiceprintLockChanged = vi.fn();
      render(modal(dana(true, locked), { onVoiceprintLockChanged }));

      fireEvent.click(screen.getByRole("button", { name: button }));

      expect(await screen.findByRole("button", { name: after })).toBeTruthy();
      expect(updateGlobalSpeaker).toHaveBeenCalledWith(7, {
        is_voiceprint_locked: !locked,
      });
      expect(deleteGlobalSpeakerEmbedding).not.toHaveBeenCalled();
      expect(onVoiceprintLockChanged).toHaveBeenCalledWith(7, !locked);
      expect(addNotification).toHaveBeenCalledWith({
        type: "success",
        message: `Dana's voiceprint is ${locked ? "unlocked" : "locked"}.`,
      });
      expect(screen.getByText("Voiceprint Active")).toBeTruthy();
      // The line saying what still changes it shows exactly while locked.
      expect(Boolean(screen.queryByText(LOCKED_LINE))).toBe(!locked);
    },
  );

  it("says what still changes a locked voiceprint", () => {
    render(modal(dana(true, true)));

    expect(screen.getByText(LOCKED_LINE)).toBeTruthy();
  });

  it("offers no lock for a person without a voiceprint", () => {
    render(modal(dana(false, false)));

    expect(screen.getByText("No Voiceprint")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Lock voiceprint" })).toBeNull();
  });

  it("still offers Unlock for a locked person whose voiceprint was cleared", () => {
    render(modal(dana(false, true)));

    expect(screen.getByRole("button", { name: "Unlock voiceprint" })).toBeTruthy();
  });

  it.each([
    { locked: false, button: "Lock voiceprint" },
    { locked: true, button: "Unlock voiceprint" },
  ])("$button carries the design system's focus ring", ({ locked, button }) => {
    render(modal(dana(true, locked)));

    // The ring the Button primitive draws (docs/DESIGN.md, "The flat canon").
    expect(screen.getByRole("button", { name: button }).className).toContain(
      "focus-visible:outline-focus-ring",
    );
  });

  it("lets the voiceprint row wrap, so Delete stays in a phone-width dialog", () => {
    render(modal(dana(true, true)));

    // jsdom has no layout. In a 375px viewport the unwrapped row put Delete
    // past the dialog's edge once the lock sat beside it.
    const row = screen.getByText("Voiceprint Active").closest(".justify-between");
    expect(row?.className).toContain("flex-wrap");
    expect(row?.contains(screen.getByRole("button", { name: "Delete" }))).toBe(true);
  });

  it("says it is saving and ignores another click meanwhile", async () => {
    const answer = answerLater();
    render(modal(dana(true, false)));

    fireEvent.click(screen.getByRole("button", { name: "Lock voiceprint" }));

    const saving = screen.getByRole("button", { name: "Saving..." });
    expect(saving.getAttribute("aria-disabled")).toBe("true");
    expect(saving.getAttribute("aria-busy")).toBe("true");
    fireEvent.click(saving);
    expect(updateGlobalSpeaker).toHaveBeenCalledTimes(1);
    await answer(dana(true, true));
    expect(screen.getByRole("button", { name: "Unlock voiceprint" })).toBeTruthy();
  });

  // In Chrome a disabled toggle lost its focus to the page body, outside the
  // dialog's focus trap (#539). jsdom keeps focus on a disabled button, so
  // this checks the toggle is never disabled.
  it("keeps the toggle focused while the lock saves", async () => {
    const answer = answerLater();
    render(modal(dana(true, false)));
    const toggle = screen.getByRole("button", { name: "Lock voiceprint" });
    toggle.focus();

    fireEvent.click(toggle);

    expect(screen.getByRole("button", { name: "Saving..." })).toBe(toggle);
    expect((toggle as HTMLButtonElement).disabled).toBe(false);
    expect(document.activeElement).toBe(toggle);
    await answer(dana(true, true));
    expect(screen.getByRole("button", { name: "Unlock voiceprint" })).toBe(toggle);
    expect(document.activeElement).toBe(toggle);
  });

  it("leaves focus where it was when the toggle did not have it", async () => {
    const answer = answerLater();
    render(modal(dana(true, false)));
    const name = screen.getByDisplayValue("Dana");
    name.focus();

    fireEvent.click(screen.getByRole("button", { name: "Lock voiceprint" }));
    await answer(dana(true, true));

    expect(document.activeElement).toBe(name);
  });

  it("keeps a late answer for Dana off Eli's dialog", async () => {
    const answer = answerLater();
    const onVoiceprintLockChanged = vi.fn();
    const view = render(modal(dana(true, false), { onVoiceprintLockChanged }));
    fireEvent.click(screen.getByRole("button", { name: "Lock voiceprint" }));

    // The dialog moves on to Eli, who is unlocked, before Dana's answer lands.
    view.rerender(
      modal(person(8, "Eli", true, false), { onVoiceprintLockChanged }),
    );
    await answer(dana(true, true));

    expect(screen.getByRole("button", { name: "Lock voiceprint" })).toBeTruthy();
    expect(screen.queryByText(LOCKED_LINE)).toBeNull();
    // The list still learns that Dana is locked.
    expect(onVoiceprintLockChanged).toHaveBeenCalledWith(7, true);
  });

  it("keeps unsaved edits and never submits the person form", async () => {
    const answer = answerLater();
    const onSave = vi.fn();
    render(modal(dana(true, false), { onSave }));
    fireEvent.change(screen.getByDisplayValue("Dana"), {
      target: { value: "Dana Q" },
    });
    fireEvent.change(
      screen.getByPlaceholderText("Additional notes about this person..."),
      { target: { value: "Chairs the meeting." } },
    );

    fireEvent.click(screen.getByRole("button", { name: "Lock voiceprint" }));
    await answer(dana(true, true));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByDisplayValue("Dana Q")).toBeTruthy();
    expect(screen.getByDisplayValue("Chairs the meeting.")).toBeTruthy();
  });

  it("keeps the lock as it was when saving it fails", async () => {
    updateGlobalSpeaker.mockReset().mockRejectedValue(new Error("offline"));
    const onVoiceprintLockChanged = vi.fn();
    render(modal(dana(true, true), { onVoiceprintLockChanged }));

    fireEvent.click(screen.getByRole("button", { name: "Unlock voiceprint" }));

    await waitFor(() =>
      expect(addNotification).toHaveBeenCalledWith({
        type: "error",
        message: "Failed to unlock Dana's voiceprint.",
      }),
    );
    expect(screen.getByRole("button", { name: "Unlock voiceprint" })).toBeTruthy();
    expect(onVoiceprintLockChanged).not.toHaveBeenCalled();
  });
});

describe("PersonModal new tag", () => {
  beforeEach(() => {
    addNotification.mockReset();
    createPeopleTag.mockReset();
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("says why a new tag was refused and keeps its name", async () => {
    createPeopleTag.mockRejectedValue(httpError(400, "Tag already exists"));
    render(modal(dana(false, false)));

    fireEvent.click(screen.getByRole("button", { name: "New Tag" }));
    const field = screen.getByPlaceholderText("New tag name...");
    fireEvent.change(field, { target: { value: "Clients" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() =>
      expect(addNotification).toHaveBeenCalledWith({
        type: "error",
        message: "Tag already exists",
      }),
    );
    expect(createPeopleTag).toHaveBeenCalledWith("Clients");
    expect(screen.getByPlaceholderText("New tag name...")).toHaveValue(
      "Clients",
    );
  });
});

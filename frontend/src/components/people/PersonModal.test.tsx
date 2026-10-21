import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PersonModal } from "./PersonModal";
import type { GlobalSpeaker } from "@/types";

const addNotification = vi.fn();
const updateGlobalSpeaker = vi.fn();
const deleteGlobalSpeakerEmbedding = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

vi.mock("@/lib/api", () => ({
  getPeopleTags: vi.fn().mockResolvedValue([]),
  createPeopleTag: vi.fn(),
  getGlobalSpeakers: vi.fn().mockResolvedValue([]),
  mergeSpeakers: vi.fn(),
  deleteGlobalSpeakerEmbedding: (...a: unknown[]) =>
    deleteGlobalSpeakerEmbedding(...a),
  updateGlobalSpeaker: (...a: unknown[]) => updateGlobalSpeaker(...a),
}));

const LOCKED_LINE = "Locked: renames and automatic learning leave it as it is.";

const dana = (voiceprint: boolean, locked: boolean): GlobalSpeaker =>
  ({
    id: 7,
    name: "Dana",
    has_voiceprint: voiceprint,
    is_voiceprint_locked: locked,
  }) as GlobalSpeaker;

const renderModal = (
  person: GlobalSpeaker,
  onVoiceprintLockChanged = vi.fn(),
) =>
  render(
    <PersonModal
      person={person}
      isOpen
      onClose={vi.fn()}
      onSave={vi.fn()}
      onVoiceprintLockChanged={onVoiceprintLockChanged}
    />,
  );

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
      renderModal(dana(true, locked), onVoiceprintLockChanged);

      fireEvent.click(screen.getByRole("button", { name: button }));

      expect(
        await screen.findByRole("button", { name: after }),
      ).toBeTruthy();
      expect(updateGlobalSpeaker).toHaveBeenCalledWith(7, {
        is_voiceprint_locked: !locked,
      });
      expect(deleteGlobalSpeakerEmbedding).not.toHaveBeenCalled();
      expect(onVoiceprintLockChanged).toHaveBeenCalledWith(7, !locked);
      expect(screen.getByText("Voiceprint Active")).toBeTruthy();
      // The line saying renames leave it alone shows exactly while locked.
      expect(Boolean(screen.queryByText(LOCKED_LINE))).toBe(!locked);
    },
  );

  it("says a locked voiceprint is left as it is by renames", () => {
    renderModal(dana(true, true));

    expect(screen.getByText(LOCKED_LINE)).toBeTruthy();
  });

  it("offers no lock for a person without a voiceprint", () => {
    renderModal(dana(false, false));

    expect(screen.getByText("No Voiceprint")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Lock voiceprint" })).toBeNull();
  });

  it("still offers Unlock for a locked person whose voiceprint was cleared", () => {
    renderModal(dana(false, true));

    expect(screen.getByRole("button", { name: "Unlock voiceprint" })).toBeTruthy();
  });

  it("keeps the lock as it was when saving it fails", async () => {
    updateGlobalSpeaker.mockReset().mockRejectedValue(new Error("offline"));
    const onVoiceprintLockChanged = vi.fn();
    renderModal(dana(true, true), onVoiceprintLockChanged);

    fireEvent.click(screen.getByRole("button", { name: "Unlock voiceprint" }));

    await waitFor(() =>
      expect(addNotification).toHaveBeenCalledWith({
        type: "error",
        message: "Failed to unlock the voiceprint.",
      }),
    );
    expect(screen.getByRole("button", { name: "Unlock voiceprint" })).toBeTruthy();
    expect(onVoiceprintLockChanged).not.toHaveBeenCalled();
  });
});

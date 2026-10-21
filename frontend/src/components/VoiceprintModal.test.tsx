import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import VoiceprintModal from "./VoiceprintModal";
import type { VoiceprintApplyResult } from "@/types";

const addNotification = vi.fn();
const applyVoiceprintAction = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

vi.mock("@/lib/api", () => ({
  applyVoiceprintAction: (...a: unknown[]) => applyVoiceprintAction(...a),
}));

const match = { id: 7, name: "Dana", similarity_score: 0.9, is_strong_match: true };

const tooShort: VoiceprintApplyResult = {
  success: true,
  has_voiceprint: true,
  matched_speaker: { id: 7, name: "Dana" },
  message: "Linked to Global Speaker: Dana",
  library_voiceprint: {
    status: "skipped_insufficient_speech",
    speech_seconds: 8,
    required_speech_seconds: 20,
  },
};

const NOTICE =
  "Voiceprint not saved: 8 s of clear speech, 20 s needed. A later recording linked to Dana with at least 20 s of clear speech will save it.";

describe("VoiceprintModal", () => {
  beforeEach(() => {
    addNotification.mockReset();
    applyVoiceprintAction.mockReset().mockResolvedValue(tooShort);
  });

  it("says when linking a voiceprint did not save it", async () => {
    render(
      <VoiceprintModal
        isOpen
        onClose={vi.fn()}
        onComplete={vi.fn()}
        recordingId="rec-1"
        extractResult={{
          embedding_extracted: true,
          matched_speaker: match,
          all_global_speakers: [{ id: 7, name: "Dana", has_voiceprint: false }],
          speaker_id: 1,
          diarization_label: "SPEAKER_00",
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() =>
      expect(addNotification).toHaveBeenCalledWith({ type: "info", message: NOTICE }),
    );
  });

  it("says once when several linked voiceprints in a batch were not saved", async () => {
    render(
      <VoiceprintModal
        isOpen
        onClose={vi.fn()}
        onComplete={vi.fn()}
        recordingId="rec-1"
        batchResults={[
          {
            diarization_label: "SPEAKER_00",
            speaker_name: "Speaker 1",
            success: true,
            matched_speaker: match,
          },
          {
            diarization_label: "SPEAKER_01",
            speaker_name: "Speaker 2",
            success: true,
            matched_speaker: match,
          },
        ]}
        allGlobalSpeakers={[{ id: 7, name: "Dana", has_voiceprint: false }]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Link to Dana" }));
    fireEvent.click(screen.getByRole("button", { name: "Link to Dana" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply All" }));

    await waitFor(() => expect(applyVoiceprintAction).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(addNotification).toHaveBeenCalledTimes(1));
    expect(addNotification).toHaveBeenCalledWith({ type: "info", message: NOTICE });
  });
});

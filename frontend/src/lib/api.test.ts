import { beforeEach, describe, expect, it, vi } from "vitest";

const patch = vi.fn();
const put = vi.fn();
const get = vi.fn();
const post = vi.fn();
const del = vi.fn();
const requestUse = vi.fn();
const responseUse = vi.fn();

vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => ({
      patch,
      put,
      get,
      post,
      delete: del,
      interceptors: {
        request: { use: requestUse },
        response: { use: responseUse },
      },
    })),
  },
}));

describe("transcript speaker API", () => {
  beforeEach(() => {
    patch.mockReset();
    post.mockReset();
  });

  it("patches utterance speaker assignments by stable utterance id with scope", async () => {
    const { updateTranscriptUtteranceSpeaker } = await import("./api");

    await updateTranscriptUtteranceSpeaker("rec-1", "utt-42", {
      name: "Dana",
      diarizationLabel: "SPEAKER_01",
      scope: "from_this_utterance_forward",
    });

    expect(patch).toHaveBeenCalledWith(
      "/transcripts/rec-1/utterances/utt-42/speaker",
      {
        new_speaker_name: "Dana",
        global_speaker_id: undefined,
        diarization_label: "SPEAKER_01",
        scope: "from_this_utterance_forward",
      },
    );
  });

  it("posts speaker suggestion acceptance by diarization label", async () => {
    const { acceptSpeakerNameSuggestion } = await import("./api");

    await acceptSpeakerNameSuggestion("rec-1", "SPEAKER_00");

    expect(post).toHaveBeenCalledWith(
      "/speakers/recordings/rec-1/speakers/SPEAKER_00/suggestions/accept",
    );
  });

  it("posts speaker suggestion rejection by diarization label", async () => {
    const { rejectSpeakerNameSuggestion } = await import("./api");

    await rejectSpeakerNameSuggestion("rec-1", "SPEAKER_00");

    expect(post).toHaveBeenCalledWith(
      "/speakers/recordings/rec-1/speakers/SPEAKER_00/suggestions/reject",
    );
  });
});

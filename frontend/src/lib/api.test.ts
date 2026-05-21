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
    get.mockReset();
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

  it("fetches utterance deltas with an after_revision cursor", async () => {
    get.mockResolvedValueOnce({
      data: {
        recording_id: "rec-1",
        revision: 9,
        utterances: [],
        tombstones: ["utt-1"],
        speakers: [],
      },
    });

    const { getTranscriptUtterances } = await import("./api");

    const response = await getTranscriptUtterances("rec-1", 7);

    expect(get).toHaveBeenCalledWith(
      "/transcripts/rec-1/utterances?after_revision=7",
    );
    expect(response.revision).toBe(9);
    expect(response.tombstones).toEqual(["utt-1"]);
  });

  it("patches utterance text by stable utterance id with the expected revision", async () => {
    const { updateTranscriptUtteranceText } = await import("./api");

    await updateTranscriptUtteranceText("rec-1", "utt-42", "Updated text", 3);

    expect(patch).toHaveBeenCalledWith(
      "/transcripts/rec-1/utterances/utt-42/text",
      {
        text: "Updated text",
        expected_revision: 3,
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

  it("fetches the admin health dashboard payload", async () => {
    get.mockResolvedValueOnce({
      data: {
        status: "ok",
        version: "2.1.0",
        summary: {
          pipeline_status: "ready",
          message: "Live and final processing prerequisites are ready.",
          blocking_reasons: [],
          degraded_reasons: [],
        },
        checks: {
          database: { status: "ok", label: "Connected", detail: "ready" },
          queue: { status: "ok", label: "Reachable", detail: "ready" },
          worker: { status: "ok", label: "Active", detail: "ready" },
          ffmpeg: { status: "ok", label: "Ready", detail: "ready" },
          transcription_model: { status: "ok", label: "Ready", detail: "ready" },
          diarization: { status: "ok", label: "Ready", detail: "ready" },
          device: { status: "ok", label: "GPU ready", detail: "ready" },
          optional_ai: { status: "info", label: "Not configured", detail: "optional" },
        },
        download: {
          in_progress: false,
          status: null,
          stage: null,
          message: null,
          progress: null,
        },
      },
    });

    const { getAdminHealth } = await import("./api");

    const response = await getAdminHealth();

    expect(get).toHaveBeenCalledWith("/system/admin-health");
    expect(response.summary.pipeline_status).toBe("ready");
  });
});

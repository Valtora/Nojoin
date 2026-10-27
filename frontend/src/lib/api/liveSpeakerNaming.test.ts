import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import api from "./client";
import { nameLiveSpeaker } from "./speakers";

const originalAdapter = api.defaults.adapter;
const requests: Request[] = [];

describe("live speaker naming transport", () => {
  beforeEach(() => {
    requests.length = 0;
    api.defaults.adapter = "fetch";
    vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
      requests.push(request);
      return new Response(JSON.stringify({ live_speaker_names: { LIVE_PROVISIONAL_1: "Alex" } }),
        { status: 200, headers: { "content-type": "application/json" } });
    }));
  });
  afterEach(() => { api.defaults.adapter = originalAdapter; vi.unstubAllGlobals(); });

  it("transports creation intent with its source revision while ordinary naming defaults off", async () => {
    const anchor = { id: "u1", revision: 7 };
    expect(await nameLiveSpeaker("r1", "LIVE_PROVISIONAL_1", "Alex", anchor, true))
      .toEqual({ LIVE_PROVISIONAL_1: "Alex" });
    expect(new URL(requests[0].url).pathname.endsWith("/speakers/recordings/r1/live/LIVE_PROVISIONAL_1")).toBe(true);
    expect(requests[0].method).toBe("PUT");
    expect(await requests[0].json()).toEqual({ name: "Alex", anchor_utterance_id: "u1", expected_revision: 7, create_person: true });
    await nameLiveSpeaker("r1", "LIVE_PROVISIONAL_1", "Alex", anchor);
    expect(await requests[1].json()).toEqual({ name: "Alex", anchor_utterance_id: "u1", expected_revision: 7 });
  });
});

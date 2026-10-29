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

  it("transports creation intent only when asked", async () => {
    expect(await nameLiveSpeaker("r1", "LIVE_PROVISIONAL_1", "Alex", true))
      .toEqual({ LIVE_PROVISIONAL_1: "Alex" });
    expect(new URL(requests[0].url).pathname.endsWith("/speakers/recordings/r1/live/LIVE_PROVISIONAL_1")).toBe(true);
    expect(requests[0].method).toBe("PUT");
    expect(await requests[0].json()).toEqual({ name: "Alex", create_person: true });
    await nameLiveSpeaker("r1", "LIVE_PROVISIONAL_1", "Alex");
    expect(await requests[1].json()).toEqual({ name: "Alex" });
  });
});

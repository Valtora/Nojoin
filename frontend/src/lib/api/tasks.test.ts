import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "./client";
import { getUserTasks } from "./tasks";

const originalAdapter = api.defaults.adapter;
const requests: Request[] = [];

describe("task list transport", () => {
  beforeEach(() => {
    requests.length = 0;
    api.defaults.adapter = "fetch";
    vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
      requests.push(request);
      return new Response("[]", { status: 200, headers: { "content-type": "application/json" } });
    }));
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
  });

  it("sends the public recording filter alongside the requested task status", async () => {
    await getUserTasks("all", { recordingId: "rec-planning" });

    const url = new URL(requests[0].url);
    expect(requests[0].method).toBe("GET");
    expect(url.pathname).toMatch(/\/tasks\/$/);
    expect(Object.fromEntries(url.searchParams)).toEqual({ status: "all", recording_id: "rec-planning" });
  });

  it("encodes multiple tag IDs as repeated parameters understood by FastAPI", async () => {
    await getUserTasks("all", { tagIds: [301, 302] });

    const url = new URL(requests[0].url);
    expect(url.pathname).toMatch(/\/tasks\/$/);
    expect(url.search).toBe("?status=all&tag_ids=301&tag_ids=302");
  });

  it("sends the recording and tag filters together", async () => {
    await getUserTasks("open", { recordingId: "rec-planning", tagIds: [7] });

    const url = new URL(requests[0].url);
    expect(url.search).toBe("?status=open&recording_id=rec-planning&tag_ids=7");
  });

  it("keeps the dashboard's default active query free of filters", async () => {
    await getUserTasks();

    expect(new URL(requests[0].url).search).toBe("?status=active");
  });
});

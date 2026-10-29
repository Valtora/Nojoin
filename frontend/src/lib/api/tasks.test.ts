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
    await getUserTasks("all", "rec-planning");

    const url = new URL(requests[0].url);
    expect(requests[0].method).toBe("GET");
    expect(url.pathname).toMatch(/\/tasks\/$/);
    expect(Object.fromEntries(url.searchParams)).toEqual({ status: "all", recording_id: "rec-planning" });
  });

  it("keeps the ordinary task list active and unfiltered", async () => {
    await getUserTasks();

    expect(Object.fromEntries(new URL(requests[0].url).searchParams)).toEqual({ status: "active" });
  });
});

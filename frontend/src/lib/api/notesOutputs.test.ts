import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import api from "./client";
import { exportContent } from "./export";
import { deleteNotesOutput, generateNotes, listNotesOutputs, updateNotesOutput } from "./notes";

// The real axios instance runs, on its fetch adapter, against a stubbed fetch.
const originalAdapter = api.defaults.adapter;
const requests: Request[] = [];
const saved = { id: 11, name: "Task list", notes: "Edited", notes_status: "completed" };

const route = (request: Request) => `${request.method} ${new URL(request.url).pathname.replace(/^.*\/transcripts/, "")}`;

describe("notes outputs transport", () => {
  beforeEach(() => {
    requests.length = 0;
    api.defaults.adapter = "fetch";
    window.URL.createObjectURL = vi.fn(() => "blob:export");
    window.URL.revokeObjectURL = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
      requests.push(request);
      return new Response(JSON.stringify(saved), { status: 200, headers: { "content-type": "application/json" } });
    }));
  });
  afterEach(() => { api.defaults.adapter = originalAdapter; vi.unstubAllGlobals(); });

  it("asks for a new set or a rerun in the generate body", async () => {
    await generateNotes("rec-1", 7, { create_output: true });
    await generateNotes("rec-1", null, { output_id: 11 });
    await generateNotes("rec-1");
    expect(requests.map(route)).toEqual(Array(3).fill("POST /rec-1/notes/generate"));
    expect(await requests[0].json()).toEqual({ notes_template_id: 7, create_output: true });
    expect(await requests[1].json()).toEqual({ notes_template_id: null, output_id: 11 });
    expect(await requests[2].json()).toEqual({ notes_template_id: null });
  });

  it("lists, saves and deletes a set at its own address", async () => {
    await listNotesOutputs("rec-1");
    expect(await updateNotesOutput("rec-1", 11, "Edited")).toEqual(saved);
    await deleteNotesOutput("rec-1", 11);
    expect(requests.map(route)).toEqual([
      "GET /rec-1/notes/outputs", "PUT /rec-1/notes/outputs/11", "DELETE /rec-1/notes/outputs/11",
    ]);
    expect(await requests[1].json()).toEqual({ notes: "Edited" });
  });

  it("exports a chosen set only when one is named", async () => {
    await exportContent("rec-1", ["notes"], "txt", {}, true, 11);
    await exportContent("rec-1", ["notes"], "txt");
    const params = requests.map((request) => new URL(request.url).searchParams);
    expect(params[0].get("notes_output_id")).toBe("11");
    expect(params[1].has("notes_output_id")).toBe(false);
  });
});

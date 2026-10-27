import { afterEach, describe, expect, it, vi } from "vitest";
import { streamChatMessage } from "./chat";

describe("chat context metadata stream", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("separates fragmented context metadata from answer tokens and preserves notes events", async () => {
    const encoder = new TextEncoder();
    const chunks = ['data: {"context":{"provisional":true,"transcript_',
      'trimmed":true}}\n\ndata: {"token":"Hello"}\n\nevent: notes_update\n\n',
      'data: {"token":" Alex"}\n\ndata: [DONE]\n\n'];
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }))));
    const token = vi.fn();
    const error = vi.fn();
    const context = vi.fn();
    const notes = vi.fn();
    await new Promise<void>((resolve) => {
      streamChatMessage("rec-1", "Who spoke?", token, resolve, error, undefined, notes, context);
    });
    expect(context).toHaveBeenCalledExactlyOnceWith({ provisional: true, transcript_trimmed: true });
    expect(token.mock.calls.flat().join("")).toBe("Hello Alex");
    expect(notes).toHaveBeenCalledTimes(1);
    expect(error).not.toHaveBeenCalled();
  });
});

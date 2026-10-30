import { afterEach, describe, expect, it, vi } from "vitest";

import { streamChatMessage } from "./chat";

const sse = (body: string) =>
  new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body));
        controller.close();
      },
    }),
    { status: 200 },
  );

describe("streamChatMessage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // The frames the chat endpoint sends for /persona (test_holiday_persona_chat_api.py).
  it("asks for the recording again after a persona command", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sse(
          'event: persona_update\ndata: {}\n\n' +
            'data: {"token": "Persona for this meeting: pirate."}\n\n' +
            "data: [DONE]\n\n",
        ),
      ),
    );
    const tokens: string[] = [];
    const recordingChanged = vi.fn();
    const done = new Promise<void>((resolve) => {
      streamChatMessage(
        "rec-1",
        "/persona pirate",
        (token) => tokens.push(token),
        resolve,
        () => undefined,
        undefined,
        recordingChanged,
      );
    });

    await done;

    expect(recordingChanged).toHaveBeenCalledTimes(1);
    expect(tokens).toEqual(["Persona for this meeting: pirate."]);
  });
});

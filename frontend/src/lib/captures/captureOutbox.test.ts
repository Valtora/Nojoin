import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Capture } from "@/lib/api";

const holds = { taken: 0, released: 0 };

vi.mock("@/lib/notes/noteOutboxes", () => ({
  holdPendingNoteWrite: () => {
    holds.taken += 1;
    let released = false;
    return () => {
      if (!released) holds.released += 1;
      released = true;
    };
  },
}));

const { CaptureOutbox, CAPTURE_RETRY_DELAYS_MS } = await import("./captureOutbox");

const saved = (id: number, captureMs: number): Capture =>
  ({
    id,
    recording_id: "rec-1",
    kind: "capture",
    capture_ms: captureMs,
    use_in_notes: false,
    title: "Capture",
    status: "PENDING",
  }) as Capture;

const refusal = (status: number) => ({
  isAxiosError: true,
  response: { status, data: { detail: `refused ${status}` } },
});

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("captures on their way to the server", () => {
  beforeEach(() => {
    holds.taken = 0;
    holds.released = 0;
  });

  it("uploads at once, at the time it was taken, and holds the page until it lands", async () => {
    const upload = vi.fn(async (_id: string, _image: Blob, ms: number) => saved(7, ms));
    const outbox = new CaptureOutbox(upload, async () => undefined, async () => "data:preview");
    const landed = vi.fn();
    outbox.onUploaded(landed);

    outbox.add("rec-1", new Blob(["x"], { type: "image/png" }), 65_400.4);

    expect(outbox.pending("rec-1")).toMatchObject([{ status: "sending", captureMs: 65_400 }]);
    expect(holds).toEqual({ taken: 1, released: 0 });
    await settle();

    expect(upload).toHaveBeenCalledWith("rec-1", expect.any(Blob), 65_400);
    expect(landed).toHaveBeenCalledWith(saved(7, 65_400));
    expect(outbox.pending("rec-1")).toEqual([]);
    expect(holds).toEqual({ taken: 1, released: 1 });
  });

  it("tries twice more after a dropped connection, then waits for Retry", async () => {
    const upload = vi.fn(async () => {
      throw { isAxiosError: true, message: "Network Error" };
    });
    const sleep = vi.fn<(ms: number) => Promise<void>>(async () => undefined);
    const outbox = new CaptureOutbox(upload, sleep, async () => null);

    const key = outbox.add("rec-1", new Blob(["x"]), 1_000);
    await settle();

    expect(upload).toHaveBeenCalledTimes(3);
    expect(sleep.mock.calls.map(([ms]) => ms)).toEqual([...CAPTURE_RETRY_DELAYS_MS]);
    expect(outbox.pending("rec-1")).toMatchObject([{ key, status: "failed" }]);
    // Still unsent, so the page still asks before it closes.
    expect(holds.released).toBe(0);

    upload.mockImplementationOnce(async () => saved(8, 1_000) as never);
    outbox.retry(key);
    expect(outbox.pending("rec-1")).toMatchObject([{ key, status: "sending" }]);
    await settle();

    expect(outbox.pending("rec-1")).toEqual([]);
    expect(holds.released).toBe(1);
  });

  it("does not try again what the server refused, and says why", async () => {
    const upload = vi.fn(async () => {
      throw refusal(415);
    });
    const outbox = new CaptureOutbox(upload, async () => undefined, async () => null);

    outbox.add("rec-1", new Blob(["x"]), 0);
    await settle();

    expect(upload).toHaveBeenCalledTimes(1);
    expect(outbox.pending("rec-1")).toMatchObject([{ status: "failed", error: "refused 415" }]);
  });

  it("lets a failed capture go on Discard", async () => {
    const outbox = new CaptureOutbox(
      async () => {
        throw refusal(404);
      },
      async () => undefined,
      async () => null,
    );
    const key = outbox.add("rec-1", new Blob(["x"]), 0);
    await settle();

    outbox.discard(key);

    expect(outbox.pending("rec-1")).toEqual([]);
    expect(holds).toEqual({ taken: 1, released: 1 });
  });

  it("keeps one list per recording, with a stable list while nothing changes", async () => {
    const outbox = new CaptureOutbox(
      () => new Promise<Capture>(() => undefined),
      async () => undefined,
      async () => null,
    );

    outbox.add("rec-1", new Blob(["x"]), 0);
    outbox.add("rec-2", new Blob(["y"]), 0);

    expect(outbox.pending("rec-1")).toHaveLength(1);
    expect(outbox.pending("rec-2")).toHaveLength(1);
    expect(outbox.pending("rec-1")).toBe(outbox.pending("rec-1"));
    expect(outbox.pending("rec-3")).toEqual([]);
  });
});

import { afterEach, expect, it, vi } from "vitest";

import {
  coalesceTranscriptReads, serialiseTranscriptReads, transcriptReadWithTimeout, TRANSCRIPT_READ_TIMEOUT_MS,
} from "./transcriptReadQueue";

afterEach(() => vi.useRealTimers());

it("serialises explicit and event reads, coalesces bursts and gives full repair priority", async () => {
  let finish!: () => void;
  const read = vi.fn().mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve; }))
    .mockResolvedValue(undefined);
  const serial = serialiseTranscriptReads(read);
  const errors = vi.fn();
  const events = coalesceTranscriptReads(serial, errors);
  const explicit = serial("full");
  events.request("delta");
  events.request("delta");
  events.request("full");
  events.request("delta");
  await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  finish();
  await explicit;
  await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(3));
  expect(read.mock.calls).toEqual([["full"], ["delta"], ["full"]]);
  expect(errors).not.toHaveBeenCalled();
  events.close();
});

it("keeps the queue usable after a rejected or synchronously throwing read", async () => {
  const read = vi.fn().mockImplementationOnce(() => { throw new Error("failed"); }).mockResolvedValue(undefined);
  const error = vi.fn();
  const queue = coalesceTranscriptReads(read, error);
  queue.request("delta");
  queue.request("full");
  await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  expect(error).toHaveBeenCalledOnce();
});

it("aborts and drops a late stalled answer, leaving the next serial read usable", async () => {
  vi.useFakeTimers();
  let signal!: AbortSignal;
  let answer!: (value: string) => void;
  let stalled = true;
  const read = serialiseTranscriptReads(() => transcriptReadWithTimeout((sentSignal) => {
    if (!stalled) return Promise.resolve("recovered");
    stalled = false;
    signal = sentSignal;
    return new Promise<string>((resolve) => { answer = resolve; });
  }));
  const first = read();
  const rejected = expect(first).rejects.toThrow(/aborted|timed out/);
  await vi.advanceTimersByTimeAsync(TRANSCRIPT_READ_TIMEOUT_MS);
  await rejected;
  expect(signal.aborted).toBe(true);
  expect(await read()).toBe("recovered");
  answer("late answer");
  expect(vi.getTimerCount()).toBe(0);
});

it("aborts a read immediately when its recording leaves", async () => {
  const lifetime = new AbortController();
  let sent!: AbortSignal;
  const request = transcriptReadWithTimeout((signal) => {
    sent = signal;
    return new Promise(() => {});
  }, lifetime.signal);
  const rejected = expect(request).rejects.toThrow("aborted");
  lifetime.abort();
  await rejected;
  expect(sent.aborted).toBe(true);
});

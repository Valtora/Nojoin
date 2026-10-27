import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL } from "./api/client";
import { resetSessionForTests, useSessionStore } from "./session";
import { TestTranscriptEventSource as Source } from "@/testTranscriptEventSource";
import {
  closeTranscriptEventStreams, subscribeTranscriptEvents,
  TRANSCRIPT_HEARTBEAT_TIMEOUT_MS, TRANSCRIPT_RECONNECT_MS, TRANSCRIPT_REPAIR_MS,
} from "./transcriptEvents";

describe("shared transcript events", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", Source);
    Source.instances = [];
    resetSessionForTests();
  });
  afterEach(() => {
    closeTranscriptEventStreams();
    resetSessionForTests();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shares one credentialled connection and releases it only after its final panel leaves", () => {
    const first = { read: vi.fn(), health: vi.fn() };
    const second = { read: vi.fn(), health: vi.fn() };
    const leaveFirst = subscribeTranscriptEvents("a/b", first)!;
    const leaveSecond = subscribeTranscriptEvents("a/b", second)!;
    expect(Source.instances).toHaveLength(1);
    const stream = Source.instances[0];
    expect(stream.url).toBe(`${API_BASE_URL}/transcripts/a%2Fb/events`);
    expect(stream.options).toEqual({ withCredentials: true });
    stream.open();
    expect(first.read).toHaveBeenLastCalledWith("full");
    expect(second.read).toHaveBeenLastCalledWith("full");
    leaveFirst();
    expect(stream.closed).toBe(false);
    leaveSecond();
    expect(stream.closed).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("observes duplicate heartbeat hints without rereading and repairs by wall clock", async () => {
    const reader = { read: vi.fn(), health: vi.fn() };
    subscribeTranscriptEvents("rec", reader);
    const stream = Source.instances[0];
    stream.open();
    stream.hint(40);
    await vi.advanceTimersByTimeAsync(15_000);
    stream.hint(40);
    await vi.advanceTimersByTimeAsync(15_000);
    stream.hint(40);
    expect(reader.read.mock.calls.map(([mode]) => mode)).toEqual(["full", "delta", "full"]);
    await vi.advanceTimersByTimeAsync(TRANSCRIPT_HEARTBEAT_TIMEOUT_MS - 1);
    expect(stream.closed).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(stream.closed).toBe(true);
    expect(reader.health).toHaveBeenLastCalledWith(false);
    expect(reader.read).toHaveBeenLastCalledWith("delta");
  });

  it("falls back immediately, closes native retries and reconnects with a full read and back-off", async () => {
    const reader = { read: vi.fn(), health: vi.fn() };
    subscribeTranscriptEvents("rec", reader);
    Source.instances[0].open();
    Source.instances[0].fail();
    expect(Source.instances[0].closed).toBe(true);
    expect(reader.read).toHaveBeenLastCalledWith("delta");
    await vi.advanceTimersByTimeAsync(TRANSCRIPT_RECONNECT_MS);
    expect(Source.instances).toHaveLength(2);
    Source.instances[1].fail();
    await vi.advanceTimersByTimeAsync(TRANSCRIPT_RECONNECT_MS);
    expect(Source.instances).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(TRANSCRIPT_RECONNECT_MS);
    expect(Source.instances).toHaveLength(3);
    Source.instances[2].open();
    expect(reader.read).toHaveBeenLastCalledWith("full");
    expect(reader.health).toHaveBeenLastCalledWith(true);
    // A late callback from the closed source cannot affect the replacement.
    Source.instances[0].fail();
    expect(Source.instances[2].closed).toBe(false);
  });

  it("closes while sign-in is required, then repairs on sign-in without another subscription", () => {
    const reader = { read: vi.fn(), health: vi.fn() };
    subscribeTranscriptEvents("rec", reader);
    Source.instances[0].open();
    useSessionStore.setState({ signInRequired: true });
    expect(Source.instances[0].closed).toBe(true);
    expect(reader.health).toHaveBeenLastCalledWith(false);
    useSessionStore.setState({ signInRequired: false });
    expect(Source.instances).toHaveLength(2);
    Source.instances[1].open();
    expect(reader.read).toHaveBeenLastCalledWith("full");
    closeTranscriptEventStreams();
    expect(Source.instances[1].closed).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps polling-only callers untouched without EventSource", () => {
    vi.stubGlobal("EventSource", undefined);
    const reader = { read: vi.fn(), health: vi.fn() };
    expect(subscribeTranscriptEvents("rec", reader)).toBeNull();
    expect(reader.read).not.toHaveBeenCalled();
    expect(reader.health).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("continues full repair even if hints never arrive", async () => {
    const reader = { read: vi.fn(), health: vi.fn() };
    subscribeTranscriptEvents("rec", reader);
    Source.instances[0].open();
    await vi.advanceTimersByTimeAsync(TRANSCRIPT_REPAIR_MS);
    expect(reader.read.mock.calls).toEqual([["full"], ["full"]]);
  });
});

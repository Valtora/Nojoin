import { API_BASE_URL } from "@/lib/api/client";
import { useSessionStore } from "@/lib/session";

export type TranscriptReadMode = "full" | "delta";
export const TRANSCRIPT_REPAIR_MS = 30_000;
export const TRANSCRIPT_HEARTBEAT_TIMEOUT_MS = 45_000;
export const TRANSCRIPT_RECONNECT_MS = 1_000;
const MAX_RECONNECT_MS = 30_000;

interface Subscriber {
  read: (mode: TranscriptReadMode) => void;
  health: (healthy: boolean) => void;
  close?: () => void;
}

/** One transport per recording in this tab; all data still uses the delta API. */
class TranscriptEvents {
  readonly subscribers = new Set<Subscriber>();
  private source: EventSource | null = null;
  private healthy = false;
  private stopped = false;
  private sequence: number | null = null;
  private reconnectDelay = TRANSCRIPT_RECONNECT_MS;
  private reconnectTimer?: ReturnType<typeof setTimeout>;
  private heartbeatTimer?: ReturnType<typeof setTimeout>;
  private repairTimer?: ReturnType<typeof setInterval>;
  private readonly unsubscribeSession: () => void;

  constructor(private readonly recordingId: string) {
    this.unsubscribeSession = useSessionStore.subscribe((state, previous) => {
      if (state.signInRequired === previous.signInRequired) return;
      if (state.signInRequired) {
        this.disconnect();
        this.setHealthy(false);
      } else {
        this.connect();
      }
    });
  }

  add(subscriber: Subscriber): void {
    this.subscribers.add(subscriber);
    subscriber.health(this.healthy);
    if (this.healthy) subscriber.read("full");
    else if (!this.source && !this.reconnectTimer) this.connect();
  }

  private requestRead(mode: TranscriptReadMode): void {
    this.subscribers.forEach((subscriber) => subscriber.read(mode));
  }

  private setHealthy(healthy: boolean): void {
    this.healthy = healthy;
    this.subscribers.forEach((subscriber) => subscriber.health(healthy));
  }

  private watchHeartbeat(): void {
    clearTimeout(this.heartbeatTimer);
    this.heartbeatTimer = setTimeout(() => this.failed(), TRANSCRIPT_HEARTBEAT_TIMEOUT_MS);
  }

  private connect(): void {
    if (this.stopped || useSessionStore.getState().signInRequired) return;
    this.reconnectTimer = undefined;
    let source: EventSource;
    try {
      source = new EventSource(
        `${API_BASE_URL}/transcripts/${encodeURIComponent(this.recordingId)}/events`,
        { withCredentials: true },
      );
    } catch {
      this.failed();
      return;
    }
    this.source = source;
    // Bound even a connection that never reaches its first open/error event.
    this.watchHeartbeat();
    source.onopen = () => {
      if (this.source !== source) return;
      this.sequence = null;
      this.setHealthy(true);
      this.watchHeartbeat();
      this.requestRead("full");
      this.repairTimer = setInterval(() => this.requestRead("full"), TRANSCRIPT_REPAIR_MS);
    };
    source.addEventListener("transcript.changed", (event) => {
      if (this.source !== source) return;
      let sequence: unknown;
      try {
        sequence = JSON.parse((event as MessageEvent<string>).data).sequence;
      } catch {
        return;
      }
      if (typeof sequence !== "number" || !Number.isSafeInteger(sequence) || sequence < 0) return;
      // Comments flush proxies, but EventSource cannot expose them. The server
      // repeats this hint for observable liveness; duplicates do not read data.
      this.watchHeartbeat();
      this.reconnectDelay = TRANSCRIPT_RECONNECT_MS;
      if (this.sequence !== null && sequence <= this.sequence) return;
      this.sequence = sequence;
      this.requestRead("delta");
    });
    source.onerror = () => {
      if (this.source === source) this.failed();
    };
  }

  private failed(): void {
    if (this.stopped) return;
    // Close native automatic reconnect before scheduling our bounded back-off.
    this.disconnect();
    this.setHealthy(false);
    this.requestRead("delta");
    if (useSessionStore.getState().signInRequired) return;
    this.reconnectTimer = setTimeout(() => this.connect(), this.reconnectDelay);
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, MAX_RECONNECT_MS);
  }

  private disconnect(): void {
    this.source?.close();
    this.source = null;
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = undefined;
    clearTimeout(this.heartbeatTimer);
    clearInterval(this.repairTimer);
  }

  close(): void {
    this.stopped = true;
    this.disconnect();
    this.unsubscribeSession();
    this.subscribers.forEach((subscriber) => subscriber.close?.());
  }
}

const feeds = new Map<string, TranscriptEvents>();

export const supportsTranscriptEvents = (): boolean => typeof EventSource !== "undefined";

/** Returns null without EventSource, leaving the existing polling path intact. */
export function subscribeTranscriptEvents(recordingId: string, subscriber: Subscriber): (() => void) | null {
  if (!supportsTranscriptEvents()) return null;
  let feed = feeds.get(recordingId);
  if (!feed) {
    feed = new TranscriptEvents(recordingId);
    feeds.set(recordingId, feed);
  }
  feed.add(subscriber);
  return () => {
    feed.subscribers.delete(subscriber);
    if (feed.subscribers.size === 0) {
      feed.close();
      if (feeds.get(recordingId) === feed) feeds.delete(recordingId);
    }
  };
}

/** Logout ends streams before awaiting network or browser navigation. */
export function closeTranscriptEventStreams(): void {
  feeds.forEach((feed) => feed.close());
  feeds.clear();
}

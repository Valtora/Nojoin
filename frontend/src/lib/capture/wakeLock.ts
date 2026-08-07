/**
 * Holds a screen wake lock for the duration of a capture.
 *
 * Half of "recorded audio is shorter than the meeting" is the device going to
 * sleep: a display that blanks and a machine that suspends stop feeding the
 * recorder, and nothing in the page can undo that after the fact. A screen wake
 * lock is the one part of it a web page is allowed to prevent, so Nojoin takes
 * one while recording.
 *
 * It does NOT stop tab freezing or discarding. There is no API for that -- a
 * page cannot opt out of Chrome's Memory Saver, and cannot read whether it is
 * on. Chrome exempts tabs actively using the microphone or sharing a screen,
 * which a recording tab is throughout, so that half needs no code and no
 * up-front warning; docs/CAPTURE.md carries it as troubleshooting. This module
 * addresses device sleep and nothing else, and saying so here is the point: it
 * would be easy to read the presence of a wake lock as meaning suspension is
 * handled.
 *
 * The browser releases the lock whenever the page stops being visible, so
 * getting it back on return to the foreground is required rather than optional.
 */

export interface WakeLockSentinelLike {
  released: boolean;
  release: () => Promise<void>;
  addEventListener?: (type: "release", listener: () => void) => void;
}

export interface WakeLockEnvironment {
  request?: (type: "screen") => Promise<WakeLockSentinelLike>;
  documentRef?: Document;
}

const readEnvironment = (): WakeLockEnvironment => {
  if (typeof navigator === "undefined") {
    return {};
  }
  const navigatorWithWakeLock = navigator as Navigator & {
    wakeLock?: { request: (type: "screen") => Promise<WakeLockSentinelLike> };
  };
  return {
    request: navigatorWithWakeLock.wakeLock
      ? (type) => navigatorWithWakeLock.wakeLock!.request(type)
      : undefined,
    documentRef: typeof document === "undefined" ? undefined : document,
  };
};

export class CaptureWakeLock {
  private sentinel: WakeLockSentinelLike | null = null;

  private active = false;

  private readonly environment: WakeLockEnvironment;

  private visibilityListener: (() => void) | null = null;

  constructor(environment: WakeLockEnvironment = readEnvironment()) {
    this.environment = environment;
  }

  get held() {
    return this.sentinel !== null && !this.sentinel.released;
  }

  /** Best-effort: an unavailable or refused lock must never fail a recording. */
  async acquire(): Promise<void> {
    this.active = true;
    this.attachVisibilityListener();
    await this.request();
  }

  async release(): Promise<void> {
    this.active = false;
    this.detachVisibilityListener();

    const sentinel = this.sentinel;
    this.sentinel = null;
    if (!sentinel || sentinel.released) {
      return;
    }
    try {
      await sentinel.release();
    } catch {
      // Releasing a lock the browser has already dropped is not a problem.
    }
  }

  private async request(): Promise<void> {
    if (!this.active || !this.environment.request || this.held) {
      return;
    }

    try {
      const sentinel = await this.environment.request("screen");
      // A release that arrives while we still want the lock is the browser
      // taking it back, typically on tab hide. Forget the sentinel so the
      // visibility handler knows to ask again.
      sentinel.addEventListener?.("release", () => {
        if (this.sentinel === sentinel) {
          this.sentinel = null;
        }
      });
      this.sentinel = sentinel;
    } catch {
      // Unsupported, blocked by policy, or refused on a hidden tab. The
      // recording carries on either way.
    }
  }

  private attachVisibilityListener() {
    const documentRef = this.environment.documentRef;
    if (!documentRef || this.visibilityListener) {
      return;
    }

    const listener = () => {
      if (documentRef.visibilityState === "visible") {
        void this.request();
      }
    };
    documentRef.addEventListener("visibilitychange", listener);
    this.visibilityListener = () =>
      documentRef.removeEventListener("visibilitychange", listener);
  }

  private detachVisibilityListener() {
    this.visibilityListener?.();
    this.visibilityListener = null;
  }
}

export const createCaptureWakeLock = (environment?: WakeLockEnvironment) =>
  new CaptureWakeLock(environment);

import { API_BASE_URL } from "@/lib/api";
import type { RecordingId } from "@/types";

import type { GuardedExitRequest } from "./shared";

export interface CaptureLifecycleOptions {
  getRecordingId: () => RecordingId | null;
  shouldGuardExit: () => boolean;
  onGuardedExit: (request: GuardedExitRequest) => void | Promise<void>;
  /**
   * Fired when the page is thawed after the browser froze it. Advisory only:
   * a frozen tab suspends timers and stops feeding the MediaRecorder, and this
   * event is not reliably dispatched on every Chromium build, so capture
   * liveness must not depend on it (issue #166).
   */
  onPageResume?: () => void;
  windowRef?: Window;
  documentRef?: Document;
}

export class CaptureLifecycle {
  private readonly getRecordingId: CaptureLifecycleOptions["getRecordingId"];

  private readonly shouldGuardExit: CaptureLifecycleOptions["shouldGuardExit"];

  private readonly onGuardedExit: CaptureLifecycleOptions["onGuardedExit"];

  private readonly onPageResume?: CaptureLifecycleOptions["onPageResume"];

  private readonly windowRef: Window;

  private readonly documentRef?: Document;

  private activeRecordingId: RecordingId | null = null;

  private guardedRecordingId: RecordingId | null = null;

  private routeSignature: string | null = null;

  private attached = false;

  constructor(options: CaptureLifecycleOptions) {
    this.getRecordingId = options.getRecordingId;
    this.shouldGuardExit = options.shouldGuardExit;
    this.onGuardedExit = options.onGuardedExit;
    this.onPageResume = options.onPageResume;
    this.windowRef = options.windowRef ?? window;
    this.documentRef =
      options.documentRef ??
      (typeof document === "undefined" ? undefined : document);
  }

  attach(initialRouteSignature: string) {
    if (this.attached) {
      return;
    }

    this.routeSignature = initialRouteSignature;
    this.attached = true;
    this.windowRef.addEventListener("pagehide", this.handlePageHide);
    this.windowRef.addEventListener("beforeunload", this.handleBeforeUnload);
    this.documentRef?.addEventListener("resume", this.handlePageResume);
  }

  detach() {
    if (!this.attached) {
      return;
    }

    this.attached = false;
    this.windowRef.removeEventListener("pagehide", this.handlePageHide);
    this.windowRef.removeEventListener("beforeunload", this.handleBeforeUnload);
    this.documentRef?.removeEventListener("resume", this.handlePageResume);
  }

  updateRecordingId(recordingId: RecordingId | null) {
    if (this.activeRecordingId !== recordingId) {
      this.guardedRecordingId = null;
      this.activeRecordingId = recordingId;
    }
  }

  updateRouteSignature(routeSignature: string) {
    if (!this.routeSignature) {
      this.routeSignature = routeSignature;
      return;
    }

    this.routeSignature = routeSignature;
  }

  resetGuard() {
    this.guardedRecordingId = null;
  }

  private readonly handlePageHide = () => {
    this.triggerGuardedExit("pagehide", true);
  };

  private readonly handleBeforeUnload = () => {
    this.triggerGuardedExit("beforeunload", true);
  };

  // A thaw is not an exit: capture should carry on, but the gap it leaves in the
  // audio needs reporting.
  private readonly handlePageResume = () => {
    this.onPageResume?.();
  };

  private triggerGuardedExit(
    reason: GuardedExitRequest["reason"],
    useBeacon: boolean,
  ) {
    const recordingId = this.getRecordingId();
    if (!recordingId || !this.shouldGuardExit()) {
      return;
    }

    if (this.guardedRecordingId === recordingId) {
      return;
    }

    this.guardedRecordingId = recordingId;
    void this.onGuardedExit({ reason, useBeacon });
  }
}

export const sendPauseBeacon = (recordingId: RecordingId) => {
  if (typeof navigator === "undefined" || !navigator.sendBeacon) {
    return false;
  }

  try {
    return navigator.sendBeacon(
      `${API_BASE_URL}/recordings/${recordingId}/pause`,
      new Blob([], { type: "text/plain" }),
    );
  } catch {
    return false;
  }
};

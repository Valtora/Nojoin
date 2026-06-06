import { API_BASE_URL } from "@/lib/api";
import type { RecordingId } from "@/types";

import type { GuardedExitRequest } from "./shared";

export interface CaptureLifecycleOptions {
  getRecordingId: () => RecordingId | null;
  shouldGuardExit: () => boolean;
  onGuardedExit: (request: GuardedExitRequest) => void | Promise<void>;
  windowRef?: Window;
}

export class CaptureLifecycle {
  private readonly getRecordingId: CaptureLifecycleOptions["getRecordingId"];

  private readonly shouldGuardExit: CaptureLifecycleOptions["shouldGuardExit"];

  private readonly onGuardedExit: CaptureLifecycleOptions["onGuardedExit"];

  private readonly windowRef: Window;

  private activeRecordingId: RecordingId | null = null;

  private guardedRecordingId: RecordingId | null = null;

  private routeSignature: string | null = null;

  private attached = false;

  constructor(options: CaptureLifecycleOptions) {
    this.getRecordingId = options.getRecordingId;
    this.shouldGuardExit = options.shouldGuardExit;
    this.onGuardedExit = options.onGuardedExit;
    this.windowRef = options.windowRef ?? window;
  }

  attach(initialRouteSignature: string) {
    if (this.attached) {
      return;
    }

    this.routeSignature = initialRouteSignature;
    this.attached = true;
    this.windowRef.addEventListener("pagehide", this.handlePageHide);
    this.windowRef.addEventListener("beforeunload", this.handleBeforeUnload);
  }

  detach() {
    if (!this.attached) {
      return;
    }

    this.attached = false;
    this.windowRef.removeEventListener("pagehide", this.handlePageHide);
    this.windowRef.removeEventListener("beforeunload", this.handleBeforeUnload);
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
import { describe, expect, it, vi } from "vitest";

import { CaptureLifecycle } from "./lifecycle";

describe("capture lifecycle", () => {
  it("dispatches a guarded exit on pagehide", () => {
    const onGuardedExit = vi.fn();
    const windowRef = new EventTarget() as Window;

    const lifecycle = new CaptureLifecycle({
      getRecordingId: () => 99,
      shouldGuardExit: () => true,
      onGuardedExit,
      windowRef,
    });

    lifecycle.attach("/recordings");

    windowRef.dispatchEvent(new Event("pagehide"));

    expect(onGuardedExit).toHaveBeenCalledTimes(1);
    expect(onGuardedExit).toHaveBeenCalledWith({
      reason: "pagehide",
      useBeacon: true,
    });
  });

  it("does not treat tab or window focus changes as guarded exits", () => {
    const onGuardedExit = vi.fn();
    const documentRef = new EventTarget() as Document;

    Object.defineProperty(documentRef, "visibilityState", {
      configurable: true,
      value: "hidden",
    });

    const lifecycle = new CaptureLifecycle({
      getRecordingId: () => 99,
      shouldGuardExit: () => true,
      onGuardedExit,
      windowRef: new EventTarget() as Window,
    });

    lifecycle.attach("/recordings/99");

    documentRef.dispatchEvent(new Event("visibilitychange"));

    expect(onGuardedExit).not.toHaveBeenCalled();
  });

  it("does not dispatch a guarded exit on route change", () => {
    const onGuardedExit = vi.fn();
    const lifecycle = new CaptureLifecycle({
      getRecordingId: () => 99,
      shouldGuardExit: () => true,
      onGuardedExit,
      windowRef: new EventTarget() as Window,
    });

    lifecycle.attach("/");
    lifecycle.updateRouteSignature("/recordings/99");
    expect(onGuardedExit).not.toHaveBeenCalled();

    lifecycle.updateRouteSignature("/recordings");
    expect(onGuardedExit).not.toHaveBeenCalled();
  });
});
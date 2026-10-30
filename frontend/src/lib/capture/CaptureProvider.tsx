"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { usePathname, useSearchParams } from "next/navigation";

import {
  createCaptureController,
  type CaptureController,
} from "./controller";
import type { CaptureState } from "./shared";
import { useNotificationStore } from "@/lib/notificationStore";

interface CaptureContextValue {
  controller: CaptureController;
  state: CaptureState;
}

interface CaptureProviderProps {
  children: ReactNode;
}

const CaptureContext = createContext<CaptureContextValue | null>(null);
// The controller alone, which never changes, for readers of single fields.
const CaptureControllerContext = createContext<CaptureController | null>(null);

/**
 * State that changes many times a second while a capture runs: the meter
 * levels on every animation frame, the elapsed time every second, and the
 * upload queue and sequence on every segment. The provider does not pass these
 * changes to `useCapture()`, since each one would re-render every consumer,
 * the recording page and the recordings rail included, and a stream of urgent
 * renders keeps a navigation's transition from finishing (issue #822). Read
 * them with `useCaptureState`, or the levels with `subscribeLiveLevels`.
 */
const VOLATILE_CAPTURE_FIELDS: ReadonlySet<keyof CaptureState> = new Set<keyof CaptureState>([
  "levels",
  "elapsedSeconds",
  "lastSequence",
  "uploadQueue",
]);

/** Whether two states differ only in the fields above. */
export function sameSharedCaptureState(previous: CaptureState, next: CaptureState): boolean {
  if (previous === next) return true;
  const keys = Object.keys(next) as (keyof CaptureState)[];
  return keys.length === Object.keys(previous).length &&
    keys.every((key) => VOLATILE_CAPTURE_FIELDS.has(key) || Object.is(previous[key], next[key]));
}

export function CaptureProvider({ children }: CaptureProviderProps) {
  const [controller] = useState<CaptureController>(() => createCaptureController());
  const [state, setState] = useState(() => controller.getState());
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const routeSignature = `${pathname || ""}?${searchParams.toString()}`;
  const [initialRouteSignature] = useState(routeSignature);

  useEffect(
    () => controller.subscribe((next) =>
      setState((previous) => (sameSharedCaptureState(previous, next) ? previous : next))),
    [controller],
  );

  useEffect(() => {
    controller.attachLifecycle(initialRouteSignature);
    void controller.refreshPausedRecording().catch(() => {});

    return () => {
      void controller.destroy();
    };
  }, [controller, initialRouteSignature]);

  useEffect(() => {
    controller.updateRouteSignature(routeSignature);
  }, [controller, routeSignature]);

  useEffect(() => {
    const match = pathname?.match(/^\/recordings\/([^/]+)\/?$/);
    // Recording IDs are route segments; Next has already parsed the pathname.
    void controller.openRecording(match ? match[1] : null).catch(() => {});
    return () => { void controller.openRecording(null); };
  }, [controller, pathname]);

  useEffect(() => {
    if (!state.error) {
      return;
    }

    useNotificationStore.getState().addNotification({
      type: "error",
      message: state.error,
    });
    controller.clearError();
  }, [controller, state.error]);

  const value = useMemo(
    () => ({ controller, state }),
    [controller, state],
  );

  return (
    <CaptureControllerContext.Provider value={controller}>
      <CaptureContext.Provider value={value}>{children}</CaptureContext.Provider>
    </CaptureControllerContext.Provider>
  );
}

function useCaptureContext() {
  const context = useContext(CaptureContext);
  if (!context) {
    throw new Error("useCapture must be used within a CaptureProvider.");
  }
  return context;
}

/**
 * One value from the capture state, re-rendering only when it changes. For the
 * fields `useCapture()` leaves out because they change many times a second.
 * `select` must return a primitive or a reference the state holds, so an
 * unchanged value compares equal.
 */
export function useCaptureState<T>(select: (state: CaptureState) => T): T {
  const controller = useContext(CaptureControllerContext);
  if (!controller) {
    throw new Error("useCaptureState must be used within a CaptureProvider.");
  }
  const read = () => select(controller.getState());
  return useSyncExternalStore(controller.subscribe, read, read);
}

/** The capture controls' capture state and actions. Leaves out the volatile fields above. */
export function useCapture() {
  const { controller, state } = useCaptureContext();

  return {
    controller,
    start: controller.start,
    pause: controller.pause,
    resume: controller.resume,
    stop: controller.stop,
    cancel: controller.cancel,
    refreshPausedRecording: controller.refreshPausedRecording,
    discardEmptyInterruptedRecording: controller.discardEmptyInterruptedRecording,
    dismissRecoveryOffer: controller.dismissRecoveryOffer,
    updateSettings: controller.updateSettings,
    dismissCoverageWarning: controller.dismissCoverageWarning,
    displayVideoTrack: controller.displayVideoTrack,
    status: state.status,
    error: state.error,
    recordingId: state.recordingId,
    pausedRecording: state.pausedRecording,
    attentionRecordings: state.attentionRecordings,
    ownedRecordingId: state.ownedRecordingId,
    runtimeActive: state.runtimeActive,
    support: state.support,
    settings: state.settings,
    finalizeRetry: state.finalizeRetry,
    stopStage: state.stopStage,
    coverageWarning: state.coverageWarning,
  };
}

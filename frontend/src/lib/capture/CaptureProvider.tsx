"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
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
  start: CaptureController["start"];
  /** The recording whose consent notice this tab, which started it, still shows. */
  consentNoticeRecordingId: string | null;
  closeConsentNotice: () => void;
}

interface CaptureProviderProps {
  children: ReactNode;
}

const CaptureContext = createContext<CaptureContextValue | null>(null);

export function CaptureProvider({ children }: CaptureProviderProps) {
  const [controller] = useState<CaptureController>(() => createCaptureController());
  const [state, setState] = useState(() => controller.getState());
  const [consentNoticeRecordingId, setConsentNoticeRecordingId] = useState<string | null>(null);
  const closeConsentNotice = useCallback(() => setConsentNoticeRecordingId(null), []);
  const start = useCallback<CaptureController["start"]>(async (name) => {
    const result = await controller.start(name);
    if (result && !result.resumed && result.consentNoticeOutcome === "no_answer") {
      setConsentNoticeRecordingId(result.recordingId);
    }
    return result;
  }, [controller]);
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const routeSignature = `${pathname || ""}?${searchParams.toString()}`;
  const [initialRouteSignature] = useState(routeSignature);

  useEffect(() => controller.subscribe(setState), [controller]);

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
    () => ({ controller, state, start, consentNoticeRecordingId, closeConsentNotice }),
    [controller, state, start, consentNoticeRecordingId, closeConsentNotice],
  );

  return <CaptureContext.Provider value={value}>{children}</CaptureContext.Provider>;
}

export function useCapture() {
  const context = useContext(CaptureContext);
  if (!context) {
    throw new Error("useCapture must be used within a CaptureProvider.");
  }

  const { controller, state, start, consentNoticeRecordingId, closeConsentNotice } = context;

  return {
    controller,
    start,
    consentNoticeRecordingId,
    closeConsentNotice,
    pause: controller.pause,
    resume: controller.resume,
    stop: controller.stop,
    cancel: controller.cancel,
    refreshPausedRecording: controller.refreshPausedRecording,
    discardEmptyInterruptedRecording: controller.discardEmptyInterruptedRecording,
    updateSettings: controller.updateSettings,
    dismissCoverageWarning: controller.dismissCoverageWarning,
    displayVideoTrack: controller.displayVideoTrack,
    status: state.status,
    levels: state.levels,
    error: state.error,
    lastSequence: state.lastSequence,
    elapsedSeconds: state.elapsedSeconds,
    recordingId: state.recordingId,
    pausedRecording: state.pausedRecording,
    runtimeActive: state.runtimeActive,
    support: state.support,
    settings: state.settings,
    finalizeRetry: state.finalizeRetry,
    stopStage: state.stopStage,
    coverageWarning: state.coverageWarning,
    uploadQueue: state.uploadQueue,
  };
}

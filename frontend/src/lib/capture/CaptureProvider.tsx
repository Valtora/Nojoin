"use client";

import {
  createContext,
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
}

interface CaptureProviderProps {
  children: ReactNode;
}

const CaptureContext = createContext<CaptureContextValue | null>(null);

export function CaptureProvider({ children }: CaptureProviderProps) {
  const [controller] = useState<CaptureController>(() => createCaptureController());
  const [state, setState] = useState(() => controller.getState());
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
    () => ({ controller, state }),
    [controller, state],
  );

  return <CaptureContext.Provider value={value}>{children}</CaptureContext.Provider>;
}

export function useCapture() {
  const context = useContext(CaptureContext);
  if (!context) {
    throw new Error("useCapture must be used within a CaptureProvider.");
  }

  const { controller, state } = context;

  return {
    controller,
    start: controller.start,
    pause: controller.pause,
    resume: controller.resume,
    stop: controller.stop,
    cancel: controller.cancel,
    refreshPausedRecording: controller.refreshPausedRecording,
    updateSettings: controller.updateSettings,
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
  };
}

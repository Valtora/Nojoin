"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, PauseCircle } from "lucide-react";

import type { Recording } from "@/types";

interface ResumeRecordingModalProps {
  isOpen: boolean;
  recording: Recording | null;
  busyAction?: "resume" | "cancel" | null;
  onResume: () => void;
  onCancel: () => void;
}

export default function ResumeRecordingModal({
  isOpen,
  recording,
  busyAction = null,
  onResume,
  onCancel,
}: ResumeRecordingModalProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  if (!mounted || !isOpen || !recording) {
    return null;
  }

  return createPortal(
    <div className="fixed inset-0 z-9999 flex items-center justify-center bg-black/50 px-4 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-2xl border border-gray-200 bg-white p-6 shadow-2xl dark:border-gray-800 dark:bg-gray-950">
        <div className="flex items-start gap-3">
          <div className="rounded-2xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-200">
            <PauseCircle className="h-5 w-5" />
          </div>
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-orange-700 dark:text-orange-300">
              Recording paused
            </p>
            <h2 className="mt-2 text-xl font-semibold text-gray-950 dark:text-white">
              Resume or discard before starting anything new
            </h2>
          </div>
        </div>

        <p className="mt-4 text-sm leading-6 text-gray-600 dark:text-gray-300">
          Nojoin found a paused recording for your account. Resume it to keep
          recording, or discard it to clear the capture lock.
        </p>

        <div className="mt-4 rounded-2xl border border-orange-200 bg-orange-50 px-4 py-3 text-sm text-orange-950 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-100">
          <p className="font-medium">{recording.name}</p>
          <p className="mt-1 opacity-80">Recording ID: {recording.id}</p>
        </div>

        <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={onCancel}
            disabled={busyAction !== null}
            className="inline-flex items-center justify-center gap-2 rounded-2xl border border-red-300 bg-white px-4 py-3 text-sm font-semibold text-red-700 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-red-500/30 dark:bg-gray-950 dark:text-red-300 dark:hover:bg-red-500/10"
          >
            {busyAction === "cancel" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Discard recording
          </button>
          <button
            type="button"
            onClick={onResume}
            disabled={busyAction !== null}
            className="inline-flex items-center justify-center gap-2 rounded-2xl bg-orange-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-orange-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busyAction === "resume" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Resume recording
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

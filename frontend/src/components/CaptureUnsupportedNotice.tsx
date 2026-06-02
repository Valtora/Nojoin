"use client";

import type { CaptureUnsupportedReason } from "@/lib/capture/shared";

const REASON_COPY: Record<CaptureUnsupportedReason | "default", string> = {
  firefox:
    "Recording capture is not available in Firefox. Use Chrome, Edge, Brave, or Arc on Windows or Linux.",
  safari:
    "Recording capture is not available in Safari. Use Chrome, Edge, Brave, or Arc on Windows or Linux.",
  macos_chromium:
    "Recording capture is not available on macOS Chromium. Open the meeting in Chromium on Windows or Linux instead.",
  mobile:
    "Recording capture is not available on mobile browsers.",
  unknown:
    "This browser does not expose the capture APIs Nojoin needs for browser recording.",
  default:
    "This browser does not support Nojoin browser recording.",
};

interface CaptureUnsupportedNoticeProps {
  reason?: CaptureUnsupportedReason;
  compact?: boolean;
}

export default function CaptureUnsupportedNotice({
  reason,
  compact = false,
}: CaptureUnsupportedNoticeProps) {
  const message = REASON_COPY[reason || "default"];

  return (
    <div
      className={`rounded-2xl border border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-100 ${compact ? "px-3 py-2 text-xs" : "px-4 py-3 text-sm"}`}
    >
      <p className="font-medium">Browser recording unavailable</p>
      <p className="mt-1 leading-5 opacity-90">{message}</p>
    </div>
  );
}
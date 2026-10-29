"use client";

interface CaptureUnsupportedNoticeProps {
  compact?: boolean;
}

export default function CaptureUnsupportedNotice({
  compact = false,
}: CaptureUnsupportedNoticeProps) {
  return (
    <div
      className={`rounded-2xl border border-status-warning-border bg-status-warning-bg text-status-warning-fg ${compact ? "px-3 py-2 text-xs" : "px-4 py-3 text-sm"}`}
    >
      <p className="font-medium">Browser recording unavailable</p>
      <p className="mt-1 leading-5 opacity-90">
        This browser does not expose the capture APIs Nojoin needs for browser recording.
      </p>
    </div>
  );
}

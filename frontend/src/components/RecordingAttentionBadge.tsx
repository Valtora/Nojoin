"use client";

import type { ReactNode } from "react";
import { AlertCircle, Pause } from "lucide-react";

import { useCapture } from "@/lib/capture/CaptureProvider";
import { cn } from "@/lib/cn";
import { RecordingStatus, type RecordingId } from "@/types";

import { Badge } from "./ui/Badge";

interface RecordingAttentionBadgeProps {
  recordingId: RecordingId;
  className?: string;
  fallback?: ReactNode;
}

/** Recovery eligibility comes from capture discovery, including browser ownership. */
export default function RecordingAttentionBadge({
  recordingId,
  className,
  fallback = null,
}: RecordingAttentionBadgeProps) {
  const { attentionRecordings } = useCapture();
  const recording = attentionRecordings.find((candidate) => candidate.id === recordingId);
  if (!recording) return fallback;

  const paused = recording.status === RecordingStatus.PAUSED;
  return (
    <Badge
      tone="warning"
      size="sm"
      className={cn("max-w-full whitespace-normal [&_svg]:shrink-0", className)}
      icon={paused ? <Pause aria-hidden="true" /> : <AlertCircle aria-hidden="true" />}
    >
      {paused ? "Paused, needs attention" : "Interrupted, needs attention"}
    </Badge>
  );
}

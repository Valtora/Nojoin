import type { ReactNode } from "react";
import { AlertCircle, Check, Loader2, Pause, UploadCloud } from "lucide-react";

import { cn } from "@/lib/cn";
import { RecordingStatus } from "@/types";

export type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger";
export type BadgeSize = "sm" | "md";

interface BadgeProps {
  tone?: BadgeTone;
  size?: BadgeSize;
  icon?: ReactNode;
  className?: string;
  children: ReactNode;
}

/**
 * The tone is carried by the label and the outline. The fill is a low-contrast
 * tint by design, so it is never the only thing distinguishing one tone from
 * another, and it is not measured by the contrast audit for that reason.
 */
const TONES: Record<BadgeTone, string> = {
  neutral: "bg-status-neutral-bg text-status-neutral-fg border-status-neutral-border",
  info: "bg-status-info-bg text-status-info-fg border-status-info-border",
  success: "bg-status-success-bg text-status-success-fg border-status-success-border",
  warning: "bg-status-warning-bg text-status-warning-fg border-status-warning-border",
  danger: "bg-status-danger-bg text-status-danger-fg border-status-danger-border",
};

const SIZES: Record<BadgeSize, string> = {
  sm: "gap-1 px-2 py-0.5 text-[0.6875rem] [&_svg]:h-3 [&_svg]:w-3",
  md: "gap-1.5 px-3 py-1 text-xs [&_svg]:h-3.5 [&_svg]:w-3.5",
};

export function Badge({ tone = "neutral", size = "md", icon, className, children }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border font-semibold whitespace-nowrap",
        TONES[tone],
        SIZES[size],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}

interface StatusDescriptor {
  tone: BadgeTone;
  label: string;
  icon: ReactNode;
}

/**
 * The recording states collapse onto the five tones rather than each owning a
 * colour. Queued, processing and note generation are all "something is
 * happening" and share the info tone; the spinner is what distinguishes them
 * from a resting state, and the label is what distinguishes them from one
 * another.
 */
const STATUS_MAP: Record<RecordingStatus, StatusDescriptor> = {
  [RecordingStatus.UPLOADING]: {
    tone: "warning",
    label: "Uploading",
    icon: <UploadCloud aria-hidden="true" />,
  },
  [RecordingStatus.PAUSED]: {
    tone: "warning",
    label: "Paused",
    icon: <Pause aria-hidden="true" />,
  },
  [RecordingStatus.RECORDED]: {
    tone: "neutral",
    label: "Recorded",
    icon: null,
  },
  [RecordingStatus.QUEUED]: {
    tone: "info",
    label: "Queued",
    icon: <Loader2 aria-hidden="true" className="animate-spin" />,
  },
  [RecordingStatus.PROCESSING]: {
    tone: "info",
    label: "Processing",
    icon: <Loader2 aria-hidden="true" className="animate-spin" />,
  },
  [RecordingStatus.PROCESSED]: {
    tone: "success",
    label: "Ready",
    icon: <Check aria-hidden="true" />,
  },
  [RecordingStatus.ERROR]: {
    tone: "danger",
    label: "Error",
    icon: <AlertCircle aria-hidden="true" />,
  },
  [RecordingStatus.CANCELLED]: {
    tone: "neutral",
    label: "Cancelled",
    icon: null,
  },
};

interface StatusBadgeProps {
  status: RecordingStatus;
  /** True while meeting notes are generating, which is a sub-state of PROCESSED. */
  generatingNotes?: boolean;
  size?: BadgeSize;
  /** Overrides the derived label without changing the tone or glyph. */
  label?: string;
  className?: string;
}

export function StatusBadge({
  status,
  generatingNotes = false,
  size = "md",
  label,
  className,
}: StatusBadgeProps) {
  const descriptor = generatingNotes
    ? {
        tone: "info" as BadgeTone,
        label: "Generating notes",
        icon: <Loader2 aria-hidden="true" className="animate-spin" />,
      }
    : STATUS_MAP[status];

  return (
    <Badge tone={descriptor.tone} size={size} icon={descriptor.icon} className={className}>
      {label ?? descriptor.label}
    </Badge>
  );
}

export default Badge;

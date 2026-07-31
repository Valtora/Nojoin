import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type SettingsStatusBadgeTone = "neutral" | "info" | "success" | "warning" | "error";

interface SettingsStatusBadgeProps {
  tone?: SettingsStatusBadgeTone;
  className?: string;
  children: ReactNode;
}

const TONE_STYLES: Record<SettingsStatusBadgeTone, string> = {
  neutral:
    "border-surface-border bg-surface-inset text-contrast-muted",
  info:
    "border-status-info-border bg-status-info-bg text-status-info-fg",
  success:
    "border-status-success-border bg-status-success-bg text-status-success-fg",
  warning:
    "border-status-warning-border bg-status-warning-bg text-status-warning-fg",
  error:
    "border-status-danger-border bg-status-danger-bg text-status-danger-fg",
};

export default function SettingsStatusBadge({
  tone = "neutral",
  className,
  children,
}: SettingsStatusBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold",
        TONE_STYLES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

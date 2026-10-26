import { SurfaceBase } from "@/components/ui/SurfaceBase";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type SettingsCalloutTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "error";

interface SettingsCalloutProps {
  tone?: SettingsCalloutTone;
  title?: string;
  message?: ReactNode;
  className?: string;
  children?: ReactNode;
}

const TONE_STYLES: Record<SettingsCalloutTone, string> = {
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

export default function SettingsCallout({
  tone = "neutral",
  title,
  message,
  className,
  children,
}: SettingsCalloutProps) {
  const body = children ?? message;

  return (
    <SurfaceBase
      data-ui-fill={tone === "neutral" ? "inset" : `status-${tone === "error" ? "danger" : tone}`}
      data-ui-border="full"
      kind="pane"
      className={cn(
        "rounded-lg border px-4 py-3 text-sm",
        TONE_STYLES[tone],
        className,
      )}
    >
      {title && <p className="font-semibold">{title}</p>}
      {body && <div className={cn(title && "mt-1")}>{body}</div>}
    </SurfaceBase>
  );
}

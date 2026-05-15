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
    "border-gray-200/80 bg-gray-50/80 text-gray-700 dark:border-gray-800 dark:bg-gray-900/70 dark:text-gray-200",
  info:
    "border-blue-200/80 bg-blue-50/80 text-blue-800 dark:border-blue-500/20 dark:bg-blue-500/10 dark:text-blue-200",
  success:
    "border-green-200/80 bg-green-50/80 text-green-800 dark:border-green-500/20 dark:bg-green-500/10 dark:text-green-200",
  warning:
    "border-amber-200/80 bg-amber-50/80 text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200",
  error:
    "border-red-200/80 bg-red-50/80 text-red-800 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-200",
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
    <div
      className={cn(
        "rounded-2xl border px-4 py-3 text-sm",
        TONE_STYLES[tone],
        className,
      )}
    >
      {title && <p className="font-semibold">{title}</p>}
      {body && <div className={cn(title && "mt-1")}>{body}</div>}
    </div>
  );
}
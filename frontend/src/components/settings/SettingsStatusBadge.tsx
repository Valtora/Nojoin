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
    "border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200",
  info:
    "border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200",
  success:
    "border-green-200 bg-green-50 text-green-800 dark:border-green-500/30 dark:bg-green-500/10 dark:text-green-200",
  warning:
    "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200",
  error:
    "border-red-200 bg-red-50 text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200",
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
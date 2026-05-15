import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type SettingsPanelVariant = "default" | "subtle" | "meta" | "field";

interface SettingsPanelProps {
  as?: "div" | "section" | "article";
  variant?: SettingsPanelVariant;
  className?: string;
  children: ReactNode;
}

const PANEL_VARIANTS: Record<SettingsPanelVariant, string> = {
  default:
    "rounded-2xl border border-gray-200/80 bg-white/90 p-5 dark:border-gray-800 dark:bg-gray-950/80",
  subtle:
    "rounded-2xl border border-gray-200/80 bg-gray-50/85 p-5 dark:border-gray-800 dark:bg-gray-900/70",
  meta:
    "rounded-2xl border border-gray-200/80 bg-white/90 p-4 dark:border-gray-800 dark:bg-gray-950/80",
  field:
    "rounded-2xl border border-gray-200/80 bg-white/90 p-4 dark:border-gray-800 dark:bg-gray-950/80",
};

export default function SettingsPanel({
  as = "div",
  variant = "default",
  className,
  children,
}: SettingsPanelProps) {
  const Component = as;

  return (
    <Component className={cn(PANEL_VARIANTS[variant], className)}>
      {children}
    </Component>
  );
}
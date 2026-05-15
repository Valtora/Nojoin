import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

interface SettingsSectionProps {
  eyebrow?: ReactNode;
  title: string;
  description?: ReactNode;
  badge?: ReactNode;
  headerAside?: ReactNode;
  width?: "compact" | "regular" | "wide" | "full";
  className?: string;
  contentClassName?: string;
  children: ReactNode;
}

const WIDTH_STYLES = {
  compact: "max-w-2xl",
  regular: "max-w-3xl",
  wide: "max-w-4xl",
  full: "max-w-none",
} as const;

export default function SettingsSection({
  eyebrow,
  title,
  description,
  badge,
  headerAside,
  width = "full",
  className,
  contentClassName,
  children,
}: SettingsSectionProps) {
  return (
    <section
      className={cn(
        "w-full rounded-[28px] border border-gray-200/80 bg-white/95 p-6 shadow-sm shadow-gray-200/60 backdrop-blur dark:border-gray-800 dark:bg-gray-950/90 dark:shadow-none",
        WIDTH_STYLES[width],
        className,
      )}
    >
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div className="max-w-2xl">
          {eyebrow && (
            <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-gray-500 dark:text-gray-400">
              {eyebrow}
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <h3 className="text-xl font-semibold text-gray-900 dark:text-white">
              {title}
            </h3>
            {badge}
          </div>

          {description && (
            <p className="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-300">
              {description}
            </p>
          )}
        </div>

        {headerAside}
      </div>

      <div className={cn("mt-5 space-y-5", contentClassName)}>{children}</div>
    </section>
  );
}
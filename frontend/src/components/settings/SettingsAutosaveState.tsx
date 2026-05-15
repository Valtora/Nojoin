import { AlertCircle, Clock3, Loader2, Save, Shield } from "lucide-react";

import { cn } from "@/lib/cn";

export type SettingsAutosaveStatus =
  | "pending"
  | "saved"
  | "saving"
  | "error"
  | "blocked";

export interface SettingsAutosaveSnapshot {
  status: SettingsAutosaveStatus;
  message?: string;
}

interface SettingsAutosaveStateProps {
  status: SettingsAutosaveStatus;
  message?: string;
  className?: string;
}

const STATUS_CONFIG = {
  pending: {
    icon: Clock3,
    defaultMessage: "Changes pending...",
    className: "contrast-helper",
  },
  saved: {
    icon: Save,
    defaultMessage: "All changes saved",
    className: "contrast-helper",
  },
  saving: {
    icon: Loader2,
    defaultMessage: "Saving changes...",
    className: "contrast-helper",
    spin: true,
  },
  error: {
    icon: AlertCircle,
    defaultMessage: "Changes could not be saved",
    className: "text-red-600 dark:text-red-300",
  },
  blocked: {
    icon: Shield,
    defaultMessage: "Password change required",
    className: "text-orange-700 dark:text-orange-300",
  },
} as const;

export default function SettingsAutosaveState({
  status,
  message,
  className,
}: SettingsAutosaveStateProps) {
  const config = STATUS_CONFIG[status];
  const Icon = config.icon;
  const shouldSpin = "spin" in config && config.spin;

  return (
    <div
      className={cn(
        "flex items-center justify-center gap-2 text-sm",
        config.className,
        className,
      )}
    >
      <Icon className={cn("h-4 w-4", shouldSpin && "animate-spin")} />
      <span>{message || config.defaultMessage}</span>
    </div>
  );
}
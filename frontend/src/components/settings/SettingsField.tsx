import type { ReactNode } from "react";

import SettingsPanel from "./SettingsPanel";

interface SettingsFieldProps {
  label: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  className?: string;
  children: ReactNode;
}

export default function SettingsField({
  label,
  description,
  icon,
  className,
  children,
}: SettingsFieldProps) {
  return (
    <SettingsPanel variant="field" className={className}>
      <div className="mb-2 text-sm font-medium text-gray-700 dark:text-gray-300">
        <span className="flex items-center gap-2">
          {icon}
          {label}
        </span>
      </div>
      {children}
      {description && <p className="mt-2 text-xs contrast-helper">{description}</p>}
    </SettingsPanel>
  );
}
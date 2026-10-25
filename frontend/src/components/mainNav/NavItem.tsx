import { ButtonBase } from '@/components/ui/nativeBases';
import React from "react";

interface NavItemProps {
  icon: React.ReactNode;
  label: string;
  isActive?: boolean;
  onClick: () => void;
  collapsed: boolean;
  badge?: number;
  id?: string;
}

export default function NavItem({
  icon,
  label,
  isActive,
  onClick,
  collapsed,
  badge,
  id,
}: NavItemProps) {
  return (
    <ButtonBase
      id={id}
      onClick={onClick}
      title={collapsed ? label : undefined}
      className={`
        w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors duration-150
        focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring
        ${
          isActive
            ? "bg-rail-item-active text-rail-item-active-fg"
            : "text-rail-fg hover:bg-rail-item-hover"
        }
        ${collapsed ? "justify-center" : ""}
      `}
    >
      <span className="shrink-0">{icon}</span>
      {!collapsed && (
        <>
          <span className="flex-1 text-left text-sm font-medium truncate">
            {label}
          </span>
          {badge !== undefined && badge > 0 && (
            <span className="text-xs bg-status-neutral-bg text-status-neutral-fg px-1.5 py-0.5 rounded-pill">
              {badge}
            </span>
          )}
        </>
      )}
    </ButtonBase>
  );
}

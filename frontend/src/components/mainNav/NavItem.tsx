import React from "react";

interface NavItemProps {
  icon: React.ReactNode;
  label: string;
  isActive?: boolean;
  onClick: () => void;
  collapsed: boolean;
  badge?: number;
  id?: string;
  disabled?: boolean;
}

export default function NavItem({
  icon,
  label,
  isActive,
  onClick,
  collapsed,
  badge,
  id,
  disabled = false,
}: NavItemProps) {
  return (
    <button
      id={id}
      onClick={() => {
        if (!disabled) {
          onClick();
        }
      }}
      disabled={disabled}
      title={collapsed ? label : undefined}
      className={`
        w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all
        ${
          isActive
            ? "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-400"
            : disabled
              ? "text-gray-400 dark:text-gray-600"
              : "text-gray-700 dark:text-gray-300 hover:bg-white/70 hover:text-orange-800 dark:hover:bg-gray-800/70"
        }
        ${disabled ? "cursor-not-allowed opacity-60" : ""}
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
            <span className="text-xs bg-gray-200 dark:bg-gray-700 px-1.5 py-0.5 rounded-full">
              {badge}
            </span>
          )}
        </>
      )}
    </button>
  );
}

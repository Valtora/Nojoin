"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface ContextMenuItem {
  label: string;
  onClick: () => void;
  className?: string;
  // icon removed
}

interface ContextMenuProps {
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
}

export default function ContextMenu({
  x,
  y,
  items,
  onClose,
}: ContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [adjustedPos, setAdjustedPos] = useState<{
    top: number;
    left: number;
  } | null>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        onClose();
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [onClose]);

  // After the menu is rendered into the DOM, measure its actual dimensions and
  // clamp the position so it never overflows the viewport on any edge.
  useLayoutEffect(() => {
    if (!menuRef.current) return;
    const rect = menuRef.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    // Horizontal: prefer x - menuWidth (left of trigger), clamp to viewport.
    let left = x - rect.width;
    if (left < 0) left = Math.min(x, vw - rect.width);
    left = Math.max(0, Math.min(left, vw - rect.width));

    // Vertical: prefer below trigger, flip above if it would overflow.
    let top = y;
    if (top + rect.height > vh) {
      top = y - rect.height;
    }
    top = Math.max(0, top);

    setAdjustedPos({ top, left });
  }, [x, y]);

  const style = adjustedPos
    ? { top: adjustedPos.top, left: adjustedPos.left, visibility: "visible" as const }
    : { top: y, left: x, visibility: "hidden" as const };

  return createPortal(
    <div
      ref={menuRef}
      className="fixed z-[var(--z-popover)] w-48 bg-surface-float rounded-lg shadow-float border border-surface-float-border py-1 overflow-hidden"
      style={style}
    >
      {items.map((item, index) => (
        <button
          key={index}
          onClick={() => {
            item.onClick();
            onClose();
          }}
          className={`w-full text-left px-4 py-2.5 text-sm hover:bg-surface-inset active:bg-action-tint transition-colors flex items-center gap-2 ${
            index !== items.length - 1
              ? "border-b border-surface-divider"
              : ""
          } ${item.className || "text-contrast-muted"}`}
        >
          {item.label}
        </button>
      ))}
    </div>,
    document.body,
  );
}

"use client";

import { useEffect, useRef } from "react";
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

  // Adjust position logic
  // If x is too close to right edge, shift left by width (approx 192px/12rem)
  // Or simply always offset to left-bottom of cursor if requested.
  // The user asked: "opens up to the left of the cursor"
  // So we default to x - width.
  const menuWidth = 192; // w-48 is 12rem = 192px

  // Basic bounds check could be added here but user specifically asked for left alignment.
  const finalX = x - menuWidth > 0 ? x - menuWidth : x;

  const style = {
    top: y,
    left: finalX,
  };

  return createPortal(
    <div
      ref={menuRef}
      className="fixed z-`999999` w-48 bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 py-1 overflow-hidden"
      style={style}
    >
      {items.map((item, index) => (
        <button
          key={index}
          onClick={() => {
            item.onClick();
            onClose();
          }}
          className={`w-full text-left px-4 py-2.5 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 active:bg-gray-200 dark:active:bg-gray-600 transition-colors flex items-center gap-2 ${
            index !== items.length - 1
              ? "border-b border-gray-100 dark:border-gray-700"
              : ""
          } ${item.className || "text-gray-700 dark:text-gray-200"}`}
        >
          {item.label}
        </button>
      ))}
    </div>,
    document.body,
  );
}

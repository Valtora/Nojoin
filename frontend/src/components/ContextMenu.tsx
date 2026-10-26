"use client";

import { ButtonBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";

interface ContextMenuItem {
  label: string;
  onClick: () => void;
  className?: string;
}

interface ContextMenuProps {
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
  /** The menu's name for assistive technology: "Actions for Weekly sync". */
  label?: string;
  /**
   * The button that opened the menu. The menu sits against it instead of at
   * x and y, follows it as the page scrolls and closes if it goes, and a
   * press on it is left to the button's own click, which toggles the menu.
   */
  anchor?: HTMLElement | null;
  /**
   * Where focus goes back to: the element that opened the menu. Given one,
   * the menu takes focus when it opens, and Escape, Tab or a chosen item
   * close it and return focus there. Without one, focus is left alone.
   */
  returnFocusTo?: HTMLElement | null;
}

/** Between an anchor and the menu, in px. */
const ANCHOR_GAP = 4;

/**
 * A menu of actions, opened by a right-click at the pointer or by a button
 * that anchors it. It is an ARIA menu: the arrow keys, Home and End move
 * between items, and Escape closes it.
 */
export default function ContextMenu({
  x,
  y,
  items,
  onClose,
  label,
  anchor = null,
  returnFocusTo = null,
}: ContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [adjustedPos, setAdjustedPos] = useState<{
    top: number;
    left: number;
  } | null>(null);
  const managesFocus = returnFocusTo !== null;
  const placed = adjustedPos !== null;

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target)) return;
      // The anchor's own click opens and closes the menu.
      if (anchor?.contains(target)) return;
      onClose();
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [onClose, anchor]);

  // After the menu is rendered into the DOM, measure its actual dimensions and
  // clamp the position so it never overflows the viewport on any edge.
  const place = useCallback(() => {
    if (!menuRef.current) return;
    const rect = menuRef.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    let left: number;
    let top: number;
    if (anchor) {
      // Right edges aligned, below the anchor, or above it when there is no
      // room below and there is above.
      const box = anchor.getBoundingClientRect();
      left = box.right - rect.width;
      top = box.bottom + ANCHOR_GAP;
      const above = box.top - ANCHOR_GAP - rect.height;
      if (top + rect.height > vh && above >= 0) top = above;
    } else {
      // Horizontal: prefer x - menuWidth (left of trigger), clamp to viewport.
      left = x - rect.width;
      if (left < 0) left = Math.min(x, vw - rect.width);

      // Vertical: prefer below trigger, flip above if it would overflow.
      top = y;
      if (top + rect.height > vh) {
        top = y - rect.height;
      }
    }
    left = Math.max(0, Math.min(left, vw - rect.width));
    top = Math.max(0, top);

    setAdjustedPos({ top, left });
  }, [x, y, anchor]);

  useLayoutEffect(() => {
    place();
  }, [place]);

  // An anchored menu stays against its button while the list scrolls, and
  // goes when the button does: its row was removed under it.
  useEffect(() => {
    if (!anchor) return;
    const follow = () => {
      if (!anchor.isConnected) {
        onClose();
        return;
      }
      place();
    };
    window.addEventListener("resize", follow);
    // Capture phase: the list scrolls in its own box, and scroll does not bubble.
    window.addEventListener("scroll", follow, true);
    return () => {
      window.removeEventListener("resize", follow);
      window.removeEventListener("scroll", follow, true);
    };
  }, [anchor, place, onClose]);

  // The first item takes focus once the menu is placed: until then it is
  // hidden, and a hidden button cannot take focus.
  useEffect(() => {
    if (managesFocus && placed) itemRefs.current[0]?.focus();
  }, [managesFocus, placed]);

  const giveFocusBack = () => returnFocusTo?.focus();

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const buttons = itemRefs.current.filter(
      (button): button is HTMLButtonElement => button !== null,
    );
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const last = buttons.length - 1;
    const next: Record<string, number> = {
      ArrowDown: current >= last ? 0 : current + 1,
      ArrowUp: current <= 0 ? last : current - 1,
      Home: 0,
      End: last,
    };
    if (event.key in next) {
      event.preventDefault();
      buttons[next[event.key]]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      // Not also the Escape of whatever opened the menu.
      event.stopPropagation();
      giveFocusBack();
      onClose();
    } else if (event.key === "Tab" && managesFocus) {
      // The menu sits at the end of the page, so the browser's Tab carries on
      // from the element that opened it instead.
      giveFocusBack();
      onClose();
    }
  };

  const style = adjustedPos
    ? { top: adjustedPos.top, left: adjustedPos.left, visibility: "visible" as const }
    : { top: y, left: x, visibility: "hidden" as const };

  return createPortal(
    <SurfaceBase kind="float"
      ref={menuRef}
      role="menu"
      aria-label={label}
      onKeyDown={handleKeyDown}
      className="fixed z-[var(--z-popover)] w-48 bg-surface-float rounded-lg shadow-float border border-surface-float-border py-1 overflow-hidden"
      style={style}
    >
      {items.map((item, index) => (
        <ButtonBase
          key={index}
          ref={(button: HTMLButtonElement | null) => {
            itemRefs.current[index] = button;
          }}
          role="menuitem"
          // Reached with the arrow keys once the menu holds focus.
          tabIndex={managesFocus ? -1 : undefined}
          onClick={() => {
            // Focus goes back first, so an item that moves it on (to the next
            // row, a dialog, a rename field) has the last word.
            giveFocusBack();
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
        </ButtonBase>
      ))}
    </SurfaceBase>,
    document.body,
  );
}

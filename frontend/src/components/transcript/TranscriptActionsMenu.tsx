"use client";

import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { MoreHorizontal, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/cn";

import Button from "../ui/Button";
import IconButton from "../ui/IconButton";
import { TOUCH_TARGET_HEIGHT } from "../ui/touchTarget";
import { useAnchoredPanel } from "../ui/useAnchoredPanel";

export interface TranscriptMenuItem {
  id: string;
  label: string;
  icon: LucideIcon;
  /** Why the item can't be chosen yet; the menu says it once, below its items. */
  disabledReason?: string;
  /**
   * Runs once the menu has closed and focus is back on its trigger, which is
   * handed over so a popover can sit against it: it stays mounted, unlike
   * the item.
   */
  onSelect: (trigger: HTMLButtonElement) => void;
}

interface TranscriptActionsMenuProps {
  /** The menu's name, which also names its trigger: "Actions for the line at 01:23". */
  label: string;
  items: readonly TranscriptMenuItem[];
  /** Keeps the trigger shown while something it opened is open. */
  active?: boolean;
  /** Places the trigger in its line. */
  triggerClassName?: string;
}

/** How long a touch on the line is held before the menu opens. */
export const LONG_PRESS_MS = 500;
/** How far a held touch may drift, in px, before it counts as a scroll. */
const LONG_PRESS_SLOP_PX = 10;
/**
 * Where a held touch is the browser's: the text, which a long press selects,
 * and fields and links.
 */
const NATIVE_PRESS = "p[data-segment-id], input, textarea, select, a[href]";
/** The click a lifted long press sends, which must not also act on the line. */
const CLICK_AFTER_PRESS_MS = 800;

/**
 * A line's ⋯ button and the menu it opens.
 *
 * The button shows while the pointer is over its line or focus is in it, and
 * always on a touch screen, where it takes the full 44 px target. Holding a
 * touch on the line (its time, speaker or padding, not its text) opens the
 * menu too; the line is the nearest element marked `data-line-actions`.
 *
 * It is an ARIA menu: opening it focuses the first item, the arrow keys, Home
 * and End move between items, Escape closes it and returns focus to the
 * button, and Tab or a press outside closes it.
 */
export default function TranscriptActionsMenu({
  label,
  items,
  active = false,
  triggerClassName,
}: TranscriptActionsMenuProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const menuId = useId();
  const reasonId = useId();
  const { panelRef, panelStyle } = useAnchoredPanel<HTMLDivElement>(open, triggerRef);
  const placed = panelStyle.visibility === "visible";
  const hasItems = items.length > 0;
  // Said once under the items, not under each one it holds back.
  const reasons = [...new Set(items.flatMap((item) => (item.disabledReason ? [item.disabledReason] : [])))];

  // The first item takes focus once the menu is placed: before that it is
  // hidden, and a hidden button can't be focused.
  useEffect(() => {
    if (open && placed) itemRefs.current[0]?.focus();
  }, [open, placed]);

  useEffect(() => {
    if (!open) return;
    const closeOnOutsidePress = (event: PointerEvent) => {
      const target = event.target as Node;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsidePress, true);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePress, true);
  }, [open, panelRef]);

  useEffect(() => {
    const line = triggerRef.current?.closest<HTMLElement>("[data-line-actions]");
    if (!line || !hasItems) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pressedAt: { x: number; y: number } | null = null;
    let openedAt = 0;
    const cancel = () => {
      clearTimeout(timer);
      pressedAt = null;
    };
    const onDown = (event: PointerEvent) => {
      if (event.pointerType !== "touch" || !event.isPrimary) return;
      if ((event.target as Element).closest("[data-line-actions]") !== line) return;
      if ((event.target as Element).closest(NATIVE_PRESS)) return;
      cancel();
      pressedAt = { x: event.clientX, y: event.clientY };
      timer = setTimeout(() => {
        pressedAt = null;
        openedAt = Date.now();
        setOpen(true);
      }, LONG_PRESS_MS);
    };
    const onMove = (event: PointerEvent) => {
      if (!pressedAt) return;
      const drift = Math.hypot(event.clientX - pressedAt.x, event.clientY - pressedAt.y);
      if (drift > LONG_PRESS_SLOP_PX) cancel();
    };
    const justOpened = () => Date.now() - openedAt < CLICK_AFTER_PRESS_MS;
    const onClick = (event: MouseEvent) => {
      if (!justOpened()) return;
      openedAt = 0;
      event.preventDefault();
      event.stopPropagation();
    };
    const onContextMenu = (event: MouseEvent) => {
      if (pressedAt || justOpened()) event.preventDefault();
    };
    line.addEventListener("pointerdown", onDown);
    line.addEventListener("pointermove", onMove);
    line.addEventListener("pointerup", cancel);
    line.addEventListener("pointercancel", cancel);
    line.addEventListener("click", onClick, true);
    line.addEventListener("contextmenu", onContextMenu);
    return () => {
      cancel();
      line.removeEventListener("pointerdown", onDown);
      line.removeEventListener("pointermove", onMove);
      line.removeEventListener("pointerup", cancel);
      line.removeEventListener("pointercancel", cancel);
      line.removeEventListener("click", onClick, true);
      line.removeEventListener("contextmenu", onContextMenu);
    };
  }, [hasItems]);

  if (!hasItems) return null;

  const close = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const select = (item: TranscriptMenuItem) => {
    if (item.disabledReason) return;
    close();
    if (triggerRef.current) item.onSelect(triggerRef.current);
  };

  const moveFocus = (event: KeyboardEvent<HTMLDivElement>) => {
    const buttons = itemRefs.current.filter((button): button is HTMLButtonElement => !!button);
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
    } else if (event.key === "Escape" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      // Not the line's own Escape (an editor's, say).
      event.stopPropagation();
      close();
    } else if (event.key === "Tab") {
      // The menu sits at the end of the page: the browser's Tab carries on
      // from the ⋯ button instead.
      setOpen(false);
      triggerRef.current?.focus();
    }
  };

  return (
    <>
      <IconButton
        ref={triggerRef}
        size="sm"
        aria-label={label}
        title="Line actions"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((wasOpen) => !wasOpen);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && !open) {
            event.preventDefault();
            setOpen(true);
          }
        }}
        className={cn(
          "h-7 w-7 pointer-coarse:h-[44px] pointer-coarse:w-[44px]",
          open || active
            ? "opacity-100"
            : "opacity-0 group-hover/line:opacity-100 group-focus-within/line:opacity-100 pointer-coarse:opacity-100",
          triggerClassName,
        )}
        icon={<MoreHorizontal aria-hidden="true" />}
      />
      {/* On the body, as the speaker popover is: a transcript group that isn't
          playing is translucent, which makes it a stacking context, and the
          lines after it would paint over a menu inside it. */}
      {open ? createPortal(
        <SurfaceBase
          data-ui-fill="card"
          data-ui-border="full" kind="float"
          ref={panelRef}
          style={panelStyle}
          className="z-[var(--z-dropdown)] flex min-w-44 flex-col rounded-lg border border-surface-border bg-surface-card p-1 shadow-float"
        >
          <div id={menuId} role="menu" aria-label={label} onKeyDown={moveFocus} className="flex flex-col gap-0.5">
            {items.map((item, index) => {
              const Icon = item.icon;
              return (
                <Button
                  key={item.id}
                  ref={(button) => {
                    itemRefs.current[index] = button;
                  }}
                  role="menuitem"
                  aria-disabled={!!item.disabledReason}
                  aria-describedby={item.disabledReason
                    ? `${reasonId}-${reasons.indexOf(item.disabledReason)}` : undefined}
                  tabIndex={-1}
                  variant="ghost"
                  size="sm"
                  iconLeft={<Icon aria-hidden="true" className="h-4 w-4" />}
                  onClick={(event) => {
                    event.stopPropagation();
                    select(item);
                  }}
                  className={cn("w-full justify-start text-sm", TOUCH_TARGET_HEIGHT)}
                >
                  {item.label}
                </Button>
              );
            })}
          </div>
          {/* Outside the menu role, which holds only its items; w-0 keeps
              the text from widening the menu past them. */}
          {reasons.map((reason, index) => (
            <p key={reason} id={`${reasonId}-${index}`}
              className="mt-0.5 w-0 min-w-full border-t border-surface-divider px-2 pb-1 pt-1.5 text-xs text-contrast-helper">
              {reason}
            </p>
          ))}
        </SurfaceBase>,
        document.body,
      ) : null}
    </>
  );
}

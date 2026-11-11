"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type FocusEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
} from 'react';
import { Info } from 'lucide-react';

import { cn } from '@/lib/cn';
import { isImeComposing } from '@/lib/imeComposition';
import IconButton from './IconButton';
import { SurfaceBase } from './SurfaceBase';
import { TOUCH_TARGET_SQUARE } from './touchTarget';
import { useAnchoredPanel } from './useAnchoredPanel';

/**
 * How long a bubble stays after the pointer leaves its trigger, so the
 * pointer can cross the gap onto the bubble without it closing (WCAG 1.4.13).
 */
const HOVER_CLOSE_DELAY_MS = 150;

/**
 * The one tooltip implementation, behind both `Tooltip` and `InfoTip`.
 *
 * A mouse shows it on hover and keyboard focus shows it on focus. A press of
 * the trigger only pins it where the trigger is an info button, the one way a
 * touch screen, which has no hover, can open it. Escape closes it from
 * anywhere and goes no further, so the dialog it sits in stays open; a press
 * outside lets a pinned one go.
 */
function useTooltip() {
  const rootRef = useRef<HTMLSpanElement>(null);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  // Escape, or a second press, hides it until the next hover, focus or press.
  const [dismissed, setDismissed] = useState(false);
  const open = !dismissed && (hovered || focused || pinned);
  // Focus that a press gives the trigger is the press's, not the keyboard's.
  const pressing = useRef(false);
  const leaving = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(leaving.current), []);

  // On the document, after every handler in the page: a field's own Escape
  // still runs, and the dialog's, on the window, sees the press handled.
  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || isImeComposing(event)) return;
      event.preventDefault();
      event.stopPropagation();
      setPinned(false);
      setDismissed(true);
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [open]);

  useEffect(() => {
    if (!pinned) return;
    const unpinOnOutsidePress = (event: PointerEvent) => {
      if (rootRef.current?.contains(event.target as Node)) return;
      setPinned(false);
    };
    document.addEventListener('pointerdown', unpinOnOutsidePress, true);
    return () => document.removeEventListener('pointerdown', unpinOnOutsidePress, true);
  }, [pinned]);

  const handlers = {
    onPointerEnter: (event: ReactPointerEvent) => {
      if (event.pointerType === 'touch') return;
      clearTimeout(leaving.current);
      setDismissed(false);
      setHovered(true);
    },
    onPointerLeave: (event: ReactPointerEvent) => {
      if (event.pointerType === 'touch') return;
      clearTimeout(leaving.current);
      leaving.current = setTimeout(() => setHovered(false), HOVER_CLOSE_DELAY_MS);
    },
    onPointerDown: () => {
      pressing.current = true;
    },
    onFocus: () => {
      if (pressing.current) {
        pressing.current = false;
        return;
      }
      setDismissed(false);
      setFocused(true);
    },
    onBlur: (event: FocusEvent) => {
      if (rootRef.current?.contains(event.relatedTarget as Node | null)) return;
      setFocused(false);
      setPinned(false);
    },
    onClick: () => {
      pressing.current = false;
    },
  };

  const togglePin = () => {
    if (pinned) {
      setPinned(false);
      setDismissed(true);
    } else {
      setDismissed(false);
      setPinned(true);
    }
  };

  return { rootRef, open, handlers, togglePin };
}

interface BubbleProps {
  id: string;
  open: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  children: ReactNode;
}

/**
 * Always in the page, hidden while closed, so a field can name it in
 * `aria-describedby` whether or not it shows. Positioned against the viewport,
 * so a dialog's scroll box does not clip it and a phone's edge does not cut it.
 */
function TooltipBubble({ id, open, anchorRef, children }: BubbleProps) {
  const { panelRef, panelStyle } = useAnchoredPanel<HTMLSpanElement>(open, anchorRef);
  return (
    <SurfaceBase
      as="span"
      ref={panelRef}
      id={id}
      data-ui-tooltip=""
      kind="float"
      role="tooltip"
      hidden={!open}
      // The panel's own width cap is the viewport's. A bubble keeps max-w-xs,
      // the width it was measured and placed at, or it widens past the edge.
      style={open ? { ...panelStyle, maxWidth: undefined } : undefined}
      className={cn(
        'z-[var(--z-dropdown)] w-max max-w-xs rounded-md px-3 py-2',
        'bg-tooltip-bg text-tooltip-fg shadow-float',
        'text-left text-xs leading-relaxed font-normal whitespace-normal break-words',
      )}
    >
      {children}
    </SurfaceBase>
  );
}

interface TooltipProps {
  content: ReactNode;
  /** The trigger. */
  children: ReactNode;
  /** The bubble's id, for a trigger that names it in `aria-describedby`. */
  id?: string;
}

/** A label shown on hover or keyboard focus of the element it wraps. */
export default function Tooltip({ content, children, id }: TooltipProps) {
  const fallbackId = useId();
  const { rootRef, open, handlers } = useTooltip();
  return (
    <span ref={rootRef} className="inline-block" {...handlers}>
      {children}
      <TooltipBubble id={id ?? fallbackId} open={open} anchorRef={rootRef}>
        {content}
      </TooltipBubble>
    </span>
  );
}

interface InfoTipProps {
  /** The button's name, such as "About the speaker limit". */
  label: string;
  content: ReactNode;
  /** The bubble's id, for a field it describes through `aria-describedby`. */
  id?: string;
  className?: string;
}

/**
 * A small "i" button that explains the control beside it. Hover or focus shows
 * the text and a press pins it, so it opens on a touch screen too. The button
 * is described by the text, and the field it explains can name the same `id`.
 */
export function InfoTip({ label, content, id, className }: InfoTipProps) {
  const fallbackId = useId();
  const bubbleId = id ?? fallbackId;
  const { rootRef, open, handlers, togglePin } = useTooltip();
  return (
    <span ref={rootRef} className={cn('inline-flex shrink-0', className)} {...handlers}>
      <IconButton
        aria-label={label}
        aria-describedby={bubbleId}
        size="sm"
        icon={<Info aria-hidden="true" />}
        className={TOUCH_TARGET_SQUARE}
        onClick={togglePin}
      />
      <TooltipBubble id={bubbleId} open={open} anchorRef={rootRef}>
        {content}
      </TooltipBubble>
    </span>
  );
}

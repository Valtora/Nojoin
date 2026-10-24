"use client";

import { useEffect, useId, useRef, useState, type FocusEvent, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

import type { SpeakerNameDecision } from "@/lib/speakerNameReview";

import Button from "../ui/Button";
import { DISABLED_LOOK, loadingButtonProps } from "../ui/loadingButton";
import { TOUCH_TARGET_HEIGHT } from "../ui/touchTarget";
import { useAnchoredPanel } from "../ui/useAnchoredPanel";

interface SpeakerNameReviewPopoverProps {
  /** The line's speaker label, which the decision is sent for. */
  label: string;
  /** The suggested name, as the line shows it. */
  name: string;
  /** Why the name was suggested, or null. */
  reason: string | null;
  onReview: (label: string, name: string, decision: SpeakerNameDecision) => Promise<void>;
  /** Where focus goes after a decision, which removes the "?" itself. */
  focusAfterDecision: () => void;
}

/**
 * The "?" after a suggested speaker name on a transcript line, and the small
 * popover it opens with Confirm and Not {name}. The button ignores clicks
 * while its decision is sent; the line drops it once the name is settled.
 * The popover closes on Escape, a press outside it, or focus leaving it.
 */
export default function SpeakerNameReviewPopover({
  label,
  name,
  reason,
  onReview,
  focusAfterDecision,
}: SpeakerNameReviewPopoverProps) {
  const [open, setOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const { panelRef, panelStyle } = useAnchoredPanel<HTMLDivElement>(open, triggerRef);
  const placed = panelStyle.visibility === "visible";

  // Confirm takes focus once the panel is placed: before that it is hidden,
  // and a hidden button can't be focused.
  useEffect(() => {
    if (open && placed) confirmRef.current?.focus();
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

  const close = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const decide = async (decision: SpeakerNameDecision) => {
    setOpen(false);
    focusAfterDecision();
    setSending(true);
    try {
      await onReview(label, name, decision);
    } finally {
      setSending(false);
    }
  };

  // Tab past the last button, or Shift+Tab before the first, leaves the
  // popover; it closes then rather than stay open behind the focus. A blur
  // with nowhere to go (Safari, on a press of the "?") is left to the
  // outside-press handler: closing here would let the press reopen it.
  const onPanelBlur = (event: FocusEvent<HTMLDivElement>) => {
    const next = event.relatedTarget as Node | null;
    if (!next || panelRef.current?.contains(next) || triggerRef.current?.contains(next)) return;
    setOpen(false);
  };

  const onPanelKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-label={`Confirm or dismiss the name ${name}`}
        title="Suggested name, not confirmed yet"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        // The pill is drawn inside, so a touch screen's 44 px target doesn't
        // swell it.
        className={`inline-flex items-center justify-center self-center rounded-pill pointer-coarse:-my-2.5 ${TOUCH_TARGET_HEIGHT} ${DISABLED_LOOK}`}
        {...loadingButtonProps(sending, undefined, (event) => {
          event.stopPropagation();
          setOpen((wasOpen) => !wasOpen);
        })}
      >
        <span
          aria-hidden="true"
          className="rounded-pill border border-status-warning-border bg-status-warning-bg px-1.5 text-xs font-bold leading-5 text-status-warning-fg"
        >
          ?
        </span>
      </button>
      {/* On the body, as the line's menu is: a translucent group is a
          stacking context the lines after it would paint over. */}
      {open
        ? createPortal(
            <div
              ref={panelRef}
              id={panelId}
              role="dialog"
              // Focusable, so a press on its text keeps focus inside it.
              tabIndex={-1}
              aria-label={`Suggested name ${name}`}
              onKeyDown={onPanelKeyDown}
              onBlur={onPanelBlur}
              style={panelStyle}
              className="z-[var(--z-dropdown)] flex min-w-44 max-w-64 flex-col gap-2 rounded-lg border border-surface-border bg-surface-card p-2 shadow-float outline-none"
            >
              {reason && <p className="text-xs text-contrast-helper">{reason}</p>}
              <div className="flex gap-1.5">
                <Button
                  ref={confirmRef}
                  variant="primary"
                  size="sm"
                  className={TOUCH_TARGET_HEIGHT}
                  onClick={(event) => {
                    event.stopPropagation();
                    void decide("confirm");
                  }}
                >
                  Confirm
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  className={`min-w-0 truncate ${TOUCH_TARGET_HEIGHT}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    void decide("dismiss");
                  }}
                >
                  Not {name}
                </Button>
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

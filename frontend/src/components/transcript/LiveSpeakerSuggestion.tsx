"use client";

import { useRef, useState, type ReactNode } from "react";
import { ButtonBase } from "@/components/ui/nativeBases";
import { TOUCH_TARGET_HEIGHT } from "@/components/ui/touchTarget";
import { describeSuggestedNameSource } from "@/lib/speakerNameReview";
import type { SpeakerNameSource } from "@/types";

interface Props {
  label: string;
  name: string;
  /** Where the name came from, said as a processed speaker's pending name says it. */
  source?: SpeakerNameSource;
  onAccept: (label: string, name: string) => Promise<void | boolean>;
  onDismiss: (label: string) => Promise<boolean>;
  focusAfterDecision: () => void;
  /**
   * "beside" keeps Accept and Dismiss at the end of the name's first line, for
   * a transcript line. "below" gives them a row of their own under the name,
   * for a speaker card, whose column is too narrow to share.
   */
  layout?: "beside" | "below";
  /** Further actions, placed after Dismiss in the same row. */
  children?: ReactNode;
}

/** Reviews a live name without treating it as the speaker's accepted identity. */
export default function LiveSpeakerSuggestion({ label, name, source, onAccept, onDismiss, focusAfterDecision,
  layout = "beside", children }: Props) {
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const decide = async (accept: boolean) => {
    if (sendingRef.current) return;
    sendingRef.current = true;
    setSending(true);
    try {
      const accepted = accept ? await onAccept(label, name) : await onDismiss(label);
      if (accepted !== false) focusAfterDecision();
    } catch (error) {
      console.error("Failed to review the live speaker suggestion", error);
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  };
  const reason = source ? describeSuggestedNameSource({ name_source: source }) : null;
  const buttonClass = `rounded px-2 text-xs text-action-text hover:bg-action-tint ${TOUCH_TARGET_HEIGHT}`;
  // The name, its mark and where it came from wrap among themselves, and
  // Accept and Dismiss stay together: beside the name's first line, or on a
  // row of their own under it. A narrow screen or a long name wraps the name,
  // never one button away from the other (#981). On a touch screen the
  // buttons' 44 px targets overhang the line rather than push it down.
  const below = layout === "below";
  return <span role="group" aria-label={`Suggested name ${name}`} data-live-suggestion-label={label}
    className={below ? "flex min-w-0 flex-col items-start gap-1" : "inline-flex min-w-0 max-w-full items-start gap-2"}>
    <span className="min-w-0 [overflow-wrap:anywhere]" data-live-suggestion-name="">
      <span className="font-bold text-foreground">{name}</span>{" "}
      <span className="inline-block rounded-pill border border-status-warning-border bg-status-warning-bg px-2 text-xs font-semibold leading-5 text-status-warning-fg">
        Suggested
      </span>
      {reason && <>{" "}<span className="text-xs text-contrast-helper">{reason}</span></>}
    </span>
    <span data-live-suggestion-actions=""
      className={below ? "flex flex-wrap items-center gap-1" : "inline-flex shrink-0 items-center gap-1 pointer-coarse:-my-2.5"}>
      <ButtonBase type="button" disabled={sending} className={buttonClass}
        aria-label={`Accept suggested name ${name}`} onClick={() => void decide(true)}>Accept</ButtonBase>
      <ButtonBase type="button" disabled={sending} className={buttonClass}
        aria-label={`Dismiss suggested name ${name}`} onClick={() => void decide(false)}>Dismiss</ButtonBase>
      {children}
    </span>
  </span>;
}

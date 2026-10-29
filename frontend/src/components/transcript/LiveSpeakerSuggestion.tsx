"use client";

import { useRef, useState } from "react";
import { ButtonBase } from "@/components/ui/nativeBases";
import { TOUCH_TARGET_HEIGHT } from "@/components/ui/touchTarget";

interface Props {
  label: string;
  name: string;
  onAccept: (label: string, name: string) => Promise<void | boolean>;
  onDismiss: (label: string) => Promise<boolean>;
  focusAfterDecision: () => void;
}

/** Reviews a live name without treating it as the speaker's accepted identity. */
export default function LiveSpeakerSuggestion({ label, name, onAccept, onDismiss, focusAfterDecision }: Props) {
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
  const buttonClass = `rounded px-2 text-xs text-action-text hover:bg-action-tint ${TOUCH_TARGET_HEIGHT}`;
  return <span className="inline-flex flex-wrap items-center gap-1" aria-label={`Suggested name ${name}`}>
    <span className="font-bold text-status-warning-fg" title="Suggested name, not confirmed yet">{name}?</span>
    <ButtonBase type="button" disabled={sending} className={buttonClass}
      aria-label={`Accept suggested name ${name}`} onClick={() => void decide(true)}>Accept</ButtonBase>
    <ButtonBase type="button" disabled={sending} className={buttonClass}
      aria-label={`Dismiss suggested name ${name}`} onClick={() => void decide(false)}>Dismiss</ButtonBase>
  </span>;
}

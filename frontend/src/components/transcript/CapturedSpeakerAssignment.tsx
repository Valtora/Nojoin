"use client";

import { useLayoutEffect, useRef, useState, type ComponentProps } from "react";
import type { TranscriptSegment, TranscriptSpeakerAssignment } from "@/types";
import SpeakerAssignmentPopover from "../SpeakerAssignmentPopover";

export type SpeakerAssignmentCommand = (
  segment: TranscriptSegment, assignment: TranscriptSpeakerAssignment,
) => boolean | void | Promise<boolean | void>;

interface CapturedSpeakerAssignmentProps extends Omit<
  ComponentProps<typeof SpeakerAssignmentPopover>, "onSelect" | "currentSpeakerLabel"
> {
  segment: TranscriptSegment;
  onUpdateSegmentSpeaker: SpeakerAssignmentCommand;
  /** Reads the current underlying handler once, bypassing a stable event wrapper. */
  captureCommand?: () => SpeakerAssignmentCommand;
}

/** A speaker choice keeps the line revision and mutation route it opened with. */
export default function CapturedSpeakerAssignment({
  segment, onUpdateSegmentSpeaker, captureCommand, targetElement, onClose, ...props
}: CapturedSpeakerAssignmentProps) {
  const [command, setCommand] = useState(() => ({
    segment, save: captureCommand?.() ?? onUpdateSegmentSpeaker, targetElement,
  }));
  // Choosing another trigger explicitly reopens the picker against that anchor.
  if (command.targetElement !== targetElement) {
    setCommand({ segment, save: captureCommand?.() ?? onUpdateSegmentSpeaker, targetElement });
  }
  const currentCommand = useRef<typeof command | null>(null);
  useLayoutEffect(() => {
    currentCommand.current = command;
    return () => { currentCommand.current = null; };
  }, [command]);
  const saving = useRef(false);
  const select = async (assignment: TranscriptSpeakerAssignment) => {
    if (saving.current) return;
    saving.current = true;
    try {
      const accepted = await command.save(command.segment, assignment);
      if (accepted !== false && currentCommand.current === command) onClose();
    } catch (error) {
      console.error("Failed to change the line's speaker", error);
    } finally {
      saving.current = false;
    }
  };
  return <SpeakerAssignmentPopover {...props}
    targetElement={command.targetElement} currentSpeakerLabel={command.segment.speaker}
    onSelect={(assignment) => void select(assignment)} onClose={onClose} />;
}

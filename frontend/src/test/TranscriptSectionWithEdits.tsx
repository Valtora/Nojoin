import type { ComponentProps } from "react";

import TranscriptSection from "@/app/(dashboard)/recordings/[id]/_components/TranscriptSection";
import { useNoteEdits } from "@/lib/notes/useNoteEdits";

type Props = Omit<ComponentProps<typeof TranscriptSection>, "noteEdits">;

/** The transcript section with its own note edits, as the recording page hands it one. */
export default function TranscriptSectionWithEdits(props: Props) {
  const noteEdits = useNoteEdits(props.recording.id);
  return <TranscriptSection {...props} noteEdits={noteEdits} />;
}

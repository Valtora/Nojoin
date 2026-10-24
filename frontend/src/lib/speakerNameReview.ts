import type { RecordingSpeaker } from "@/types";

/** What a person decides about a suggested speaker name. */
export type SpeakerNameDecision = "confirm" | "dismiss";

/**
 * Why a speaker carries a suggested name, shown beside Confirm and Not
 * {name}: "Recognised by voice", "Suggested by AI". Null for a source with
 * nothing to explain.
 */
export function describeSuggestedNameSource(
  speaker: Pick<RecordingSpeaker, "name_source">,
): string | null {
  switch (speaker.name_source) {
    case "voiceprint":
      return "Recognised by voice";
    case "llm":
      return "Suggested by AI";
    case "heuristic":
      return "Introduced themselves";
    // A name the user gave that the final pass could not settle on one
    // speaker: its speech went to several, it met another name, or the voice
    // didn't match the person.
    case "user":
      return "You gave this name earlier";
    default:
      return null;
  }
}

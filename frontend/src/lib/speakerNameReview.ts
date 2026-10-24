import type { RecordingSpeaker } from "@/types";

/** What a person decides about a suggested speaker name. */
export type SpeakerNameDecision = "confirm" | "dismiss";

/**
 * Why a speaker carries a suggested name, shown beside Confirm and Not
 * {name}: "Recognised by voice (82%)", "Suggested by AI". Null for a source
 * with nothing to explain.
 */
export function describeSuggestedNameSource(
  speaker: Pick<RecordingSpeaker, "name_source" | "identity_confidence">,
): string | null {
  switch (speaker.name_source) {
    case "voiceprint": {
      const confidence = speaker.identity_confidence;
      return confidence === null || confidence === undefined
        ? "Recognised by voice"
        : `Recognised by voice (${Math.round(confidence * 100)}%)`;
    }
    case "llm":
      return "Suggested by AI";
    case "heuristic":
      return "Introduced themselves";
    // A name given during the meeting to part of what the final pass made
    // one speaker, which it could not carry over to all of them.
    case "user":
      return "Your name for part of this speech";
    default:
      return null;
  }
}

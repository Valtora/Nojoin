import type {
  LibraryVoiceprintOutcome,
  RecordingSpeaker,
  SpeakerSplitResult,
} from "@/types";

/**
 * The least clear speech, from one recording, a person's voiceprint is saved
 * from. Mirrors `LIBRARY_VOICEPRINT_MIN_SPEECH_S` in
 * backend/processing/embedding.py; responses carry it as
 * `required_speech_seconds`, and this is for the places that have no response.
 */
export const REQUIRED_SPEECH_SECONDS = 20;

/** Seconds as shown to the person: rounded down, so 19.96 s never reads as 20 s. */
export const formatSeconds = (seconds: number): string =>
  String(Math.floor(seconds * 10) / 10);

/**
 * The notice to show when linking a speaker did not save or update the
 * person's voiceprint, or null when it did (or nothing was offered).
 */
export function describeLibraryVoiceprint(
  outcome: LibraryVoiceprintOutcome | null | undefined,
  personName?: string | null,
): string | null {
  if (!outcome) return null;
  const whose = personName ? `${personName}'s` : "the person's";
  const who = personName ?? "this person";
  const needed = formatSeconds(outcome.required_speech_seconds);
  switch (outcome.status) {
    // A person without a voiceprint is never identified automatically, so the
    // voiceprint comes only from a recording someone links to them.
    case "skipped_insufficient_speech":
      return (
        `Voiceprint not saved: ${formatSeconds(outcome.speech_seconds ?? 0)} s ` +
        `of clear speech, ${needed} s needed. A later recording linked to ` +
        `${who} with at least ${needed} s of clear speech will save it.`
      );
    case "skipped_drift":
      return (
        `Voiceprint not updated: this speaker sounds too unlike ${whose} ` +
        "saved voiceprint. Check that the name is right."
      );
    case "skipped_locked":
      return (
        `Voiceprint not updated: ${whose} voiceprint is locked. To let it ` +
        "learn again, delete it in the person's details under People; the " +
        `next recording linked to ${who} with at least ${needed} s of clear ` +
        "speech then saves a new one."
      );
    case "skipped_outdated":
      return (
        "Voiceprint not updated yet: this recording's voiceprint was made by " +
        "an older version of Nojoin. It is re-extracted automatically and " +
        "counts towards the person's voiceprint after that."
      );
    case "saved":
    case "folded":
      return null;
  }
}

/** One notice per distinct message across the speakers a rename or promote returned. */
export function libraryVoiceprintNotices(
  speakers: Pick<RecordingSpeaker, "library_voiceprint" | "global_speaker">[],
): string[] {
  const notices = speakers
    .map((speaker) =>
      describeLibraryVoiceprint(
        speaker.library_voiceprint,
        speaker.global_speaker?.name,
      ),
    )
    .filter((notice): notice is string => notice !== null);
  return [...new Set(notices)];
}

const mostSpeech = (outcome: LibraryVoiceprintOutcome): string =>
  outcome.speech_seconds === null
    ? ""
    : ` (the most was ${formatSeconds(outcome.speech_seconds)} s)`;

/** What a split did to the voiceprint of the person the clips moved to. */
function describeSplitTarget(result: SpeakerSplitResult): string {
  const outcome = result.library_voiceprint;
  const name = result.name;
  const needed = formatSeconds(outcome.required_speech_seconds);
  switch (outcome.status) {
    case "saved":
      return `${name}'s voiceprint was saved from the moved clips.`;
    case "folded":
      return `The moved clips were blended into ${name}'s voiceprint.`;
    case "skipped_drift":
      return (
        `${name}'s voiceprint was left as it was: the moved clips sound too ` +
        "unlike it. Check that the name is right."
      );
    default: {
      const tooShort =
        `no recording's moved clips hold ${needed} s of clear speech` +
        `${mostSpeech(outcome)}.`;
      // A person who already has a voiceprint gets later recordings folded
      // in, which needs no floor, so there is nothing still to save.
      if (result.has_voiceprint) {
        return `${name}'s voiceprint was left as it was: ${tooShort}`;
      }
      return (
        `${name} has no voiceprint yet: ${tooShort} A later recording linked ` +
        `to ${name} with at least ${needed} s of clear speech will save it.`
      );
    }
  }
}

/** What a split did to the voiceprint of the person the clips came from. */
function describeSplitOriginal(
  outcome: LibraryVoiceprintOutcome,
  name: string,
): string {
  const needed = formatSeconds(outcome.required_speech_seconds);
  if (outcome.status === "saved") {
    return `${name}'s voiceprint was rebuilt from the clips that remain.`;
  }
  return (
    `${name} has no voiceprint now: no recording's remaining clips hold ` +
    `${needed} s of clear speech${mostSpeech(outcome)}, and an older ` +
    "voiceprint would still include the moved clips. A later recording " +
    `linked to ${name} with at least ${needed} s of clear speech will save ` +
    "a new one."
  );
}

/** One sentence per person on what splitting a person did to their voiceprints. */
export function describeSplitVoiceprints(
  result: SpeakerSplitResult,
  originalName: string,
): string[] {
  return [
    describeSplitTarget(result),
    describeSplitOriginal(result.original_library_voiceprint, originalName),
  ];
}

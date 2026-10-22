import { describe, expect, it } from "vitest";

import {
  describeLibraryVoiceprint,
  describeSplitVoiceprints,
  libraryVoiceprintNotices,
} from "./libraryVoiceprint";
import type { LibraryVoiceprintOutcome, SpeakerSplitResult } from "@/types";

const outcome = (
  status: LibraryVoiceprintOutcome["status"],
  speech_seconds: number | null = 12,
): LibraryVoiceprintOutcome => ({
  status,
  speech_seconds,
  required_speech_seconds: 20,
});

describe("describeLibraryVoiceprint", () => {
  it("names who a later recording must be linked to", () => {
    expect(
      describeLibraryVoiceprint(outcome("skipped_insufficient_speech"), "Dana"),
    ).toBe("Voiceprint not saved: 12 s of clear speech, 20 s needed. A later recording linked to Dana with at least 20 s of clear speech will save it.");
  });

  it("says how much speech there was and how much is needed", () => {
    expect(describeLibraryVoiceprint(outcome("skipped_insufficient_speech"))).toBe(
      "Voiceprint not saved: 12 s of clear speech, 20 s needed. A later recording linked to this person with at least 20 s of clear speech will save it.",
    );
  });

  it("never rounds a shortfall up to the floor", () => {
    expect(
      describeLibraryVoiceprint(outcome("skipped_insufficient_speech", 19.96)),
    ).toContain("19.9 s of clear speech, 20 s needed");
  });

  it("names the person when the guard kept their voiceprint", () => {
    expect(describeLibraryVoiceprint(outcome("skipped_drift"), "Dana")).toBe(
      "Voiceprint not updated: this speaker sounds too unlike Dana's saved voiceprint. Check that the name is right.",
    );
  });

  it("points a locked voiceprint to Unlock, which keeps it, not to deleting it", () => {
    expect(describeLibraryVoiceprint(outcome("skipped_locked"), "Dana")).toBe(
      "Voiceprint not updated: Dana's voiceprint is locked. To let this speaker update it, choose Unlock voiceprint in Dana's details under People, then link the speaker again. Lock it again afterwards to keep it.",
    );
    expect(describeLibraryVoiceprint(outcome("skipped_locked"))).toBe(
      "Voiceprint not updated: the person's voiceprint is locked. To let this speaker update it, choose Unlock voiceprint in the person's details under People, then link the speaker again. Lock it again afterwards to keep it.",
    );
  });

  it("says an outdated recording voiceprint counts once it is re-extracted", () => {
    expect(describeLibraryVoiceprint(outcome("skipped_outdated", null))).toBe(
      "Voiceprint not updated yet: this recording's voiceprint was made by an older version of Nojoin. It is re-extracted automatically and counts towards the person's voiceprint after that.",
    );
  });

  it("stays quiet when the voiceprint was saved or folded, or nothing was offered", () => {
    expect(describeLibraryVoiceprint(outcome("saved", 25))).toBeNull();
    expect(
      describeLibraryVoiceprint(outcome("saved_from_other_recordings", 30)),
    ).toBeNull();
    expect(describeLibraryVoiceprint(outcome("folded", 5))).toBeNull();
    expect(describeLibraryVoiceprint(null)).toBeNull();
    expect(describeLibraryVoiceprint(undefined)).toBeNull();
  });
});

describe("libraryVoiceprintNotices", () => {
  it("gives one notice per distinct message", () => {
    const short = { library_voiceprint: outcome("skipped_insufficient_speech") };
    expect(
      libraryVoiceprintNotices([
        short,
        short,
        { library_voiceprint: outcome("saved", 30) },
        { library_voiceprint: null },
      ]),
    ).toEqual([
      "Voiceprint not saved: 12 s of clear speech, 20 s needed. A later recording linked to this person with at least 20 s of clear speech will save it.",
    ]);
  });
});

describe("describeSplitVoiceprints", () => {
  const split = (
    target: LibraryVoiceprintOutcome,
    original: LibraryVoiceprintOutcome,
    hasVoiceprint: boolean,
  ): SpeakerSplitResult => ({
    id: 8,
    name: "Robin",
    has_voiceprint: hasVoiceprint,
    is_voiceprint_locked: hasVoiceprint,
    library_voiceprint: target,
    original_library_voiceprint: original,
  });

  it("says each voiceprint was rebuilt only when it was", () => {
    expect(
      describeSplitVoiceprints(
        split(outcome("saved", 25), outcome("skipped_insufficient_speech", 12), true),
        "Dana",
      ),
    ).toEqual([
      "Robin's voiceprint was saved from the moved clips.",
      "Dana has no voiceprint now: no recording's remaining clips hold 20 s of clear speech (the most was 12 s), none of Dana's other recordings can rebuild it, and an older voiceprint would still include the moved clips. A later recording linked to Dana with at least 20 s of clear speech will save a new one.",
    ]);
  });

  it("says when the original's voiceprint came from their other recordings", () => {
    expect(
      describeSplitVoiceprints(
        split(outcome("saved", 25), outcome("saved_from_other_recordings", 30), true),
        "Dana",
      )[1],
    ).toBe(
      "Dana's voiceprint was rebuilt from Dana's other recordings: no recording's remaining clips hold 20 s of clear speech.",
    );
  });

  it("says when the moved clips were too short or too unlike the person", () => {
    expect(
      describeSplitVoiceprints(
        split(outcome("skipped_insufficient_speech", 12), outcome("saved", 25), false),
        "Dana",
      ),
    ).toEqual([
      "Robin has no voiceprint yet: no recording's moved clips hold 20 s of clear speech (the most was 12 s). A later recording linked to Robin with at least 20 s of clear speech will save it.",
      "Dana's voiceprint was rebuilt from the clips that remain.",
    ]);
    expect(
      describeSplitVoiceprints(
        split(outcome("skipped_drift", 25), outcome("saved", 25), true),
        "Dana",
      )[0],
    ).toBe(
      "Robin's voiceprint was left as it was: the moved clips sound too unlike it. Check that the name is right.",
    );
    expect(
      describeSplitVoiceprints(
        split(outcome("skipped_insufficient_speech", null), outcome("saved", 25), true),
        "Dana",
      )[0],
    ).toBe(
      // Robin's voiceprint already exists, so later recordings fold into it:
      // there is nothing left to save.
      "Robin's voiceprint was left as it was: no recording's moved clips hold 20 s of clear speech.",
    );
  });
});

import { describe, expect, it } from "vitest";
import type { TranscriptSegment } from "@/types";
import { liveSpeakerSourceReference } from "./liveSpeakerSource";

const malformed: [string, Partial<TranscriptSegment>][] = [
  ["missing source identity", { id: undefined }],
  ["empty source identity", { id: "" }],
  ["missing source revision", { revision: undefined }],
  ["zero revision", { revision: 0 }],
  ["negative revision", { revision: -1 }],
  ["fractional revision", { revision: 1.5 }],
  ["non-number revision", { revision: Number.NaN }],
  ["infinite revision", { revision: Number.POSITIVE_INFINITY }],
];

describe("live speaker source validation", () => {
  it.each(malformed)("rejects %s without depending on API validation", (_name, changes) => {
    const segment: TranscriptSegment = { id: "u1", revision: 4, start: 0, end: 2,
      text: "Words", speaker: "LIVE_PROVISIONAL_1", locked: true, ...changes };
    expect(liveSpeakerSourceReference(segment)).toBeNull();
  });
});

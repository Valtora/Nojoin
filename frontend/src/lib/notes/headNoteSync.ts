/** The API's limit, `TIMELINE_NOTE_BODY_MAX_CHARS` in `timeline_note_public.py`. */
export const NOTE_BODY_MAX_CHARS = 50_000;

// The characters Python's `str.strip()` removes, which is how the API trims a
// note. `String.prototype.trim` differs: it keeps U+001C-U+001F and U+0085,
// and removes U+FEFF.
const isOuterSpace = (code: number) =>
  (code >= 0x09 && code <= 0x0d) ||
  (code >= 0x1c && code <= 0x20) ||
  code === 0x85 ||
  code === 0xa0 ||
  code === 0x1680 ||
  (code >= 0x2000 && code <= 0x200a) ||
  code === 0x2028 ||
  code === 0x2029 ||
  code === 0x202f ||
  code === 0x205f ||
  code === 0x3000;

/** A note's text as the API stores it. */
export const stripNote = (text: string): string => {
  let start = 0;
  let end = text.length;
  while (start < end && isOuterSpace(text.charCodeAt(start))) start += 1;
  while (end > start && isOuterSpace(text.charCodeAt(end - 1))) end -= 1;
  return text.slice(start, end);
};

const SURROGATE_PAIR = /[\uD800-\uDBFF][\uDC00-\uDFFF]/g;

/** A note's length as the API counts it: code points, not UTF-16 units. */
export const noteLength = (text: string): number =>
  text.length - (text.match(SURROGATE_PAIR)?.length ?? 0);

/** A version 4 UUID without `crypto.randomUUID`, which needs a secure context. */
export const newNoteId = (): string => {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
};

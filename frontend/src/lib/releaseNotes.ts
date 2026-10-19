/**
 * Shapes a GitHub release body for display in Settings > Updates.
 *
 * Bodies are written from `.github/release-notes-template.md`, which carries
 * maintainer guidance in HTML comments (GitHub hides them, a Markdown renderer
 * does not) and folds minor commits into a `<details>` block. The renderer keeps
 * no raw HTML, so both are rewritten here as plain Markdown.
 */

export interface PreparedReleaseNotes {
  /**
   * The text before the first section: the container image digests and how to
   * verify them. Shown collapsed, because an operator reading what changed
   * rarely needs a digest.
   */
  images: string | null;
  /** Everything from the first section heading on. */
  notes: string;
}

// An unterminated comment runs to the end of the body, as it would in HTML.
const HTML_COMMENT = /<!--[\s\S]*?(?:-->|$)/g;
const SUMMARY = /<summary>([\s\S]*?)<\/summary>/g;
const DETAILS_TAG = /<\/?details>/g;
// The page already names the release, so the body's own title is dropped.
const LEADING_TITLE = /^\s*#{1,2} [^\n]*\n/;
const FIRST_SECTION = /^### /m;

/**
 * Removing one comment can join the text either side of it into a new `<!--`,
 * so this repeats until nothing changes.
 */
function stripComments(text: string): string {
  let previous: string;
  let current = text;
  do {
    previous = current;
    current = current.replace(HTML_COMMENT, "");
  } while (current !== previous);
  return current;
}

export function prepareReleaseNotes(body: string): PreparedReleaseNotes {
  const text = stripComments(body.replace(/\r\n?/g, "\n"))
    .replace(SUMMARY, (_, summary: string) => `#### ${summary.trim()}`)
    .replace(DETAILS_TAG, "")
    .replace(LEADING_TITLE, "")
    .replace(/\n{3,}/g, "\n\n");

  const firstSection = text.search(FIRST_SECTION);
  if (firstSection > 0) {
    const images = text.slice(0, firstSection).trim();
    return { images: images || null, notes: text.slice(firstSection).trim() };
  }

  return { images: null, notes: text.trim() };
}

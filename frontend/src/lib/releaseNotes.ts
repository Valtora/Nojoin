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

const HTML_COMMENT = /<!--[\s\S]*?-->/g;
const SUMMARY = /<summary>([\s\S]*?)<\/summary>/g;
const DETAILS_TAG = /<\/?details>/g;
// The page already names the release, so the body's own title is dropped.
const LEADING_TITLE = /^\s*#{1,2} [^\n]*\n/;
const FIRST_SECTION = /^### /m;

export function prepareReleaseNotes(body: string): PreparedReleaseNotes {
  const text = body
    .replace(/\r\n?/g, "\n")
    .replace(HTML_COMMENT, "")
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

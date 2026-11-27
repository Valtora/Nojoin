/**
 * Shapes a GitHub release body for display in Settings > Updates.
 *
 * Bodies are written from `.github/release-notes-template.md`, which carries
 * maintainer guidance in HTML comments (GitHub hides them, a Markdown renderer
 * does not) and folds parts of the body into `<details>` blocks. The renderer
 * keeps no raw HTML, so both are rewritten here as plain Markdown.
 *
 * Two shapes are in use. Up to 3.1.0 the body opened with the image digests
 * and ended with the commit list under a Changes heading. From 3.2.0 it is a
 * short summary, with the digests and the commit list folded into two
 * `<details>` blocks at the end.
 */

export interface PreparedReleaseNotes {
  /**
   * The container image digests and how to verify them. Shown collapsed,
   * because an operator reading what changed rarely needs a digest.
   */
  images: string | null;
  /** The notes themselves, shown open. */
  notes: string;
  /**
   * The commits since the previous release, shown collapsed. Only bodies from
   * 3.2.0 on keep them apart; in an older body they are part of `notes`.
   */
  commits?: string;
}

/** The `<summary>` of each folded block in a body from 3.2.0 on. */
export const IMAGES_SUMMARY = "Container images";
export const COMMITS_SUMMARY = "Commits";

// An unterminated comment runs to the end of the body, as it would in HTML.
const HTML_COMMENT = /<!--[\s\S]*?(?:-->|$)/g;
const SUMMARY = /<summary>([\s\S]*?)<\/summary>/g;
const DETAILS_TAG = /<\/?details>/g;
// The page already names the release, so the body's own title is dropped.
const LEADING_TITLE = /^\s*#{1,2} [^\n]*\n/;
const FIRST_SECTION = /^### /m;
const LEADING_SUMMARY = /^\s*<summary>([\s\S]*?)<\/summary>/;

interface FoldedBlock {
  start: number;
  end: number;
  summary: string;
  content: string;
}

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

/** Rewrites what the renderer cannot show as Markdown it can. */
function toMarkdown(text: string): string {
  return text
    .replace(SUMMARY, (_, summary: string) => `#### ${summary.trim()}`)
    .replace(DETAILS_TAG, "");
}

function collapseBlankLines(text: string): string {
  return text.replace(/\n{3,}/g, "\n\n");
}

/**
 * The outermost `<details>` blocks, each with its summary and what follows
 * it. A block can hold others: the commit list folds its minor commits into
 * one of its own.
 */
function foldedBlocks(text: string): FoldedBlock[] {
  const blocks: FoldedBlock[] = [];
  let depth = 0;
  let start = 0;
  for (const tag of text.matchAll(DETAILS_TAG)) {
    const index = tag.index ?? 0;
    if (!tag[0].startsWith("</")) {
      if (depth === 0) {
        start = index;
      }
      depth += 1;
    } else if (depth > 0) {
      depth -= 1;
      if (depth === 0) {
        const inner = text.slice(start + "<details>".length, index);
        const summary = inner.match(LEADING_SUMMARY);
        blocks.push({
          start,
          end: index + tag[0].length,
          summary: summary ? summary[1].trim() : "",
          content: summary ? inner.slice(summary[0].length) : inner,
        });
      }
    }
  }
  return blocks;
}

/** A body from 3.2.0 on: the summary open, digests and commits folded. */
function prepareShortNotes(
  text: string,
  blocks: FoldedBlock[],
  images: FoldedBlock,
): PreparedReleaseNotes {
  const commits = blocks.find((block) => block.summary === COMMITS_SUMMARY);
  let notes = text;
  for (const block of [images, commits]
    .filter((block): block is FoldedBlock => block !== undefined)
    .sort((a, b) => b.start - a.start)) {
    notes = notes.slice(0, block.start) + notes.slice(block.end);
  }

  const prepared: PreparedReleaseNotes = {
    images: collapseBlankLines(toMarkdown(images.content)).trim() || null,
    notes: collapseBlankLines(toMarkdown(notes).replace(LEADING_TITLE, "")).trim(),
  };
  const commitList = commits ? collapseBlankLines(toMarkdown(commits.content)).trim() : "";
  if (commitList) {
    prepared.commits = commitList;
  }
  return prepared;
}

/** A body up to 3.1.0: the digests before the first section, the rest open. */
function prepareLongNotes(text: string): PreparedReleaseNotes {
  const shaped = collapseBlankLines(toMarkdown(text).replace(LEADING_TITLE, ""));

  const firstSection = shaped.search(FIRST_SECTION);
  if (firstSection > 0) {
    const images = shaped.slice(0, firstSection).trim();
    return { images: images || null, notes: shaped.slice(firstSection).trim() };
  }

  return { images: null, notes: shaped.trim() };
}

export function prepareReleaseNotes(body: string): PreparedReleaseNotes {
  const text = stripComments(body.replace(/\r\n?/g, "\n"));
  const blocks = foldedBlocks(text);
  const images = blocks.find((block) => block.summary === IMAGES_SUMMARY);
  return images ? prepareShortNotes(text, blocks, images) : prepareLongNotes(text);
}

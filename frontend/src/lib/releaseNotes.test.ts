import { describe, expect, it } from "vitest";

import { prepareReleaseNotes } from "./releaseNotes";

const BODY = [
  "## Nojoin 9.9.0",
  "",
  "Container images for this release.",
  "",
  "- `ghcr.io/example/api@sha256:abc`",
  "",
  "### Highlights",
  "",
  "<!-- Maintainer: one bullet per item. -->",
  "",
  "- **Faster uploads.** Segments register in constant time.",
  "",
  "### Changes",
  "",
  "<details>",
  "<summary>Other changes (2)</summary>",
  "",
  "- **release:** Bump the version",
  "</details>",
].join("\r\n");

describe("prepareReleaseNotes", () => {
  it("drops maintainer comments and the release title", () => {
    const { notes, images } = prepareReleaseNotes(BODY);

    expect(notes).not.toContain("<!--");
    expect(notes).not.toContain("Maintainer");
    expect(images).not.toContain("Nojoin 9.9.0");
  });

  it("splits the image digests from the notes", () => {
    const { notes, images } = prepareReleaseNotes(BODY);

    expect(images).toContain("sha256:abc");
    expect(notes.startsWith("### Highlights")).toBe(true);
    expect(notes).not.toContain("sha256");
  });

  it("turns a details block into a heading the renderer can show", () => {
    const { notes } = prepareReleaseNotes(BODY);

    expect(notes).toContain("#### Other changes (2)");
    expect(notes).not.toMatch(/<\/?(details|summary)>/);
  });

  it("keeps a body with no sections whole", () => {
    expect(prepareReleaseNotes("Just a line.")).toEqual({
      images: null,
      notes: "Just a line.",
    });
  });
});

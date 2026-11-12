// @vitest-environment node

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  COMMITS_SUMMARY,
  IMAGES_SUMMARY,
  prepareReleaseNotes,
} from "./releaseNotes";

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

  it("leaves no comment opener behind, even one built from two halves", () => {
    const { notes } = prepareReleaseNotes(
      "### Notes\n\nBefore <!<!-- x -->-- hidden --> after <!-- unterminated",
    );

    expect(notes).not.toContain("<!--");
    expect(notes).not.toContain("hidden");
    expect(notes).not.toContain("unterminated");
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

// A body from 3.2.0 on, as release.yml fills the template.
const SHORT_BODY = [
  "<!--",
  "Maintainer: the body of the GitHub release.",
  "-->",
  "",
  "## Nojoin 9.9.0",
  "",
  "### Before you upgrade",
  "",
  "<!-- One line for each breaking change. -->",
  "",
  "- **Each user accepts the terms of use.** Alice Example accepts once.",
  "",
  "### What's new",
  "",
  "- **Test connection** asks for a model.",
  "",
  "### Upgrade",
  "",
  "```bash",
  "docker compose pull",
  "```",
  "",
  "The full notes are in [9.9.0.md](https://example.invalid/9.9.0.md).",
  "",
  "<details>",
  "<summary>Container images</summary>",
  "",
  "Verify before deploying.",
  "",
  "- `ghcr.io/example/api@sha256:abc`",
  "",
  "</details>",
  "",
  "<details>",
  "<summary>Commits</summary>",
  "",
  "#### New Features",
  "",
  "- **ui:** Add a tooltip (#1) (`aaa1111`)",
  "",
  "<details>",
  "<summary>Other changes (1)</summary>",
  "",
  "- **deps:** Bump a pin (#2) (`bbb2222`)",
  "</details>",
  "",
  "</details>",
].join("\r\n");

const TEMPLATE = path.resolve(
  __dirname,
  "../../../.github/release-notes-template.md",
);

describe("prepareReleaseNotes on a short body", () => {
  it("opens on the notes and leaves the folded blocks out of them", () => {
    const { notes } = prepareReleaseNotes(SHORT_BODY);

    expect(notes.startsWith("### Before you upgrade")).toBe(true);
    expect(notes).toContain("[9.9.0.md](https://example.invalid/9.9.0.md)");
    expect(notes).not.toContain("Nojoin 9.9.0");
    expect(notes).not.toContain("sha256");
    expect(notes).not.toContain("aaa1111");
    expect(notes).not.toMatch(/<!--|<\/?(details|summary)>/);
  });

  it("folds the image digests apart", () => {
    const { images } = prepareReleaseNotes(SHORT_BODY);

    expect(images).toBe(
      "Verify before deploying.\n\n- `ghcr.io/example/api@sha256:abc`",
    );
  });

  it("folds the commit list apart, minor commits under a heading", () => {
    const { commits } = prepareReleaseNotes(SHORT_BODY);

    expect(commits).toContain("#### New Features");
    expect(commits).toContain("#### Other changes (1)");
    expect(commits).toContain("bbb2222");
    expect(commits).not.toMatch(/<\/?(details|summary)>/);
  });

  it("gives an older body no separate commit list", () => {
    expect(prepareReleaseNotes(BODY).commits).toBeUndefined();
  });

  it("reads the template's own folded blocks", () => {
    const template = readFileSync(TEMPLATE, "utf8");
    expect(template).toContain(`<summary>${IMAGES_SUMMARY}</summary>`);
    expect(template).toContain(`<summary>${COMMITS_SUMMARY}</summary>`);

    const { images, notes, commits } = prepareReleaseNotes(
      template
        .replaceAll("{{VERSION}}", "9.9.0")
        .replace("{{IMAGE_DIGESTS}}", "- `ghcr.io/example/api@sha256:abc`")
        .replace("{{CHANGELOG}}", "#### Bug Fixes\n\n- **ui:** Fix it (`ccc3333`)"),
    );

    expect(images).toContain("sha256:abc");
    expect(commits).toContain("ccc3333");
    expect(notes.startsWith("### ")).toBe(true);
    expect(notes).toContain("docs/releases/9.9.0.md");
    expect(notes).not.toMatch(/sha256|ccc3333|\{\{/);
  });
});

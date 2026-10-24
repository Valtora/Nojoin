// @vitest-environment node

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { cssDeclarations } from "@/test/cssDeclarations";

/**
 * Tiptap injects ProseMirror's base rules in a <style> tag, which the
 * nonce-based style-src in proxy.ts refuses, so the editors had none of them
 * (#501). Every editor now passes `injectCSS: false` and the rules live in
 * globals.css. This compares that copy with the installed Tiptap, so an
 * upgrade that changes the base rules fails here, and it fails on an editor
 * created without the option.
 */
const TIPTAP_CORE = path.resolve(__dirname, "../../node_modules/@tiptap/core/dist/index.js");
const GLOBALS = path.resolve(__dirname, "globals.css");
const SRC = path.resolve(__dirname, "..");

// A colour literal in Tiptap's sheet may be a token in ours.
const COLOUR = /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?)\([^)]*\)|\b(?:black|white)\b/gi;
const TOKEN = /var\(--[\w-]+\)/g;

function tiptapBaseStyle(): string {
  const source = readFileSync(TIPTAP_CORE, "utf8");
  return source.match(/const style = `([\s\S]*?)`;/)?.[1] ?? "";
}

const normalise = (value: string) => value.replace(/\s+/g, " ").trim();

function sameValue(library: string, ours: string): boolean {
  return (
    normalise(library) === normalise(ours) ||
    normalise(library).replace(COLOUR, "<colour>") === normalise(ours).replace(TOKEN, "<colour>")
  );
}

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(full);
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [full] : [];
  });
}

describe("ProseMirror base styles", () => {
  const library = cssDeclarations(tiptapBaseStyle());
  const ours = cssDeclarations(readFileSync(GLOBALS, "utf8"));

  it("finds Tiptap's base style (guards the parser)", () => {
    expect(library.length).toBeGreaterThan(20);
    expect(library.map((rule) => rule.selector)).toContain(".ProseMirror-gapcursor");
  });

  it("carries every rule Tiptap would inject", () => {
    const missing = library
      .filter(
        (rule) =>
          !ours.some(
            (own) =>
              own.selector === rule.selector &&
              own.property === rule.property &&
              own.atRules === rule.atRules &&
              sameValue(rule.value, own.value),
          ),
      )
      .map((rule) => `${rule.atRules} ${rule.selector} { ${rule.property}: ${rule.value} }`.trim());

    expect(missing).toEqual([]);
  });

  it("creates every editor without Tiptap's style tag", () => {
    const editors = sourceFiles(SRC).filter((file) =>
      /\buseEditor\(|\bnew Editor\(/.test(readFileSync(file, "utf8")),
    );
    const without = editors
      .filter((file) => {
        const text = readFileSync(file, "utf8");
        const created = text.match(/\buseEditor\(|\bnew Editor\(/g)?.length ?? 0;
        const optedOut = text.match(/\binjectCSS:\s*false\b/g)?.length ?? 0;
        return optedOut < created;
      })
      .map((file) => path.relative(SRC, file));

    expect(editors.length).toBeGreaterThanOrEqual(2);
    expect(without).toEqual([]);
  });
});

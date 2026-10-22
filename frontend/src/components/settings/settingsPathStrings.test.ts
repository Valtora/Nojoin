import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { SETTINGS_CATEGORIES } from "./settingsCategories";

/**
 * Source guard: every "Settings > X" in the app names a Settings category that
 * exists. Categories have been renamed and merged, and a stale path sends
 * people looking for a page that is not there.
 *
 * A heuristic over source text, not a parser. The words after "Settings > "
 * must begin with a category label, followed by the end of the string or
 * anything but a letter, so "Settings > Your AI after finishing" passes and
 * "Settings > AI shows" does not.
 */
const SRC = path.resolve(__dirname, "../..");
const SETTINGS_PATH = /Settings > ([^"'`\n]+)/g;
const LABELS = Object.values(SETTINGS_CATEGORIES).map((category) => category.label);

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [full] : [];
  });
}

function namesACategory(rest: string): boolean {
  return LABELS.some(
    (label) => rest.startsWith(label) && !/^[A-Za-z]/.test(rest.slice(label.length)),
  );
}

function findStalePaths(): string[] {
  const found: string[] = [];
  for (const file of sourceFiles(SRC)) {
    const lines = readFileSync(file, "utf8").split("\n");
    lines.forEach((line, index) => {
      for (const match of line.matchAll(SETTINGS_PATH)) {
        if (!namesACategory(match[1])) {
          found.push(`${path.relative(SRC, file)}:${index + 1}: Settings > ${match[1]}`);
        }
      }
    });
  }
  return found;
}

describe("Settings paths in app text", () => {
  it("names only Settings categories that exist", () => {
    expect(findStalePaths()).toEqual([]);
  });
});

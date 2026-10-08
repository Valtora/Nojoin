import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Source guard for the Corner style setting (docs/DESIGN.md, "Corner radii").
 *
 * Every radius answers to --radius-scale except a true circle. Two ways to
 * opt out by accident are cheap to write and invisible at the default scale,
 * so they are caught here rather than by a screenshot under Square:
 *
 * - a text-bearing pill written with rounded-full (it stays a pill under
 *   Square; it should be rounded-pill), recognised by horizontal padding on
 *   the same class string;
 * - an arbitrary rounded-[...] length that does not go through the scale.
 */
const SRC = path.resolve(__dirname, "..");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [full] : [];
  });
}

function violations(pattern: (line: string) => boolean): string[] {
  return sourceFiles(SRC).flatMap((file) =>
    readFileSync(file, "utf8")
      .split("\n")
      .flatMap((line, index) =>
        pattern(line) ? [`${path.relative(SRC, file)}:${index + 1}`] : [],
      ),
  );
}

describe("corner radius usage", () => {
  it("writes text-bearing pills as rounded-pill, not rounded-full", () => {
    expect(
      violations((line) => /\brounded-full\b/.test(line) && /\bpx-\d/.test(line)),
    ).toEqual([]);
  });

  it("routes arbitrary radii through --radius-scale", () => {
    expect(
      violations(
        (line) => /\brounded(-[a-z]{1,2})?-\[/.test(line) && !line.includes("--radius-scale"),
      ),
    ).toEqual([]);
  });
});

// @vitest-environment node

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * A frontend build must not download anything (#703). next/font/google
 * fetches its fonts from Google at build time, so a build with no network
 * failed. Geist and Geist Mono are served from the files in fonts/ through
 * next/font/local instead, one call per Google Fonts subset.
 */
const APP = __dirname;
const SRC = path.resolve(APP, "..");
const FONTS = path.join(APP, "fonts");

const read = (file: string) => readFileSync(file, "utf8");

function sourceFiles(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...sourceFiles(full));
    else if (/\.(ts|tsx|js|mjs|css)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      files.push(full);
    }
  }
  return files;
}

interface Face {
  src: string;
  family: string;
  range: string;
  preload: boolean;
}

/** Each localFont call in fonts/geist.ts, in declaration order. */
function faces(): Face[] {
  const calls = read(path.join(FONTS, "geist.ts")).split("localFont({").slice(1);
  return calls.map((call) => ({
    src: /src: "\.\/([^"]+)"/.exec(call)?.[1] ?? "",
    family: /prop: "font-family", value: "([^"]+)"/.exec(call)?.[1] ?? "",
    range: /prop: "unicode-range",\s*value:\s*"([^"]+)"/.exec(call)?.[1] ?? "",
    preload: /preload: true/.test(call),
  }));
}

const SUBSET_ORDER = ["cyrillic-ext", "cyrillic", "symbols2", "vietnamese", "latin-ext", "latin"];

/** "geist-mono-latin-ext.woff2" is Geist Mono's latin-ext subset. */
function subsetOf(face: Face): { mono: boolean; subset: string } {
  const mono = face.src.startsWith("geist-mono-");
  return { mono, subset: face.src.slice(mono ? "geist-mono-".length : "geist-".length, -".woff2".length) };
}

describe("self-hosted fonts", () => {
  it("never loads a font from Google", () => {
    const offenders = sourceFiles(SRC).filter((file) =>
      /next\/font\/google|fonts\.(googleapis|gstatic)\.com/.test(read(file)),
    );
    expect(offenders.map((file) => path.relative(SRC, file))).toEqual([]);
  });

  it("declares every font file in fonts/ exactly once", () => {
    const files = readdirSync(FONTS).filter((name) => name.endsWith(".woff2"));
    const declared = faces().map((face) => face.src);
    expect([...declared].sort()).toEqual([...files].sort());
    expect(new Set(declared).size).toBe(declared.length);
  });

  it("gives each face its family and a unicode-range", () => {
    for (const face of faces()) {
      expect(face.family, face.src).toBe(subsetOf(face).mono ? "Geist Mono" : "Geist");
      expect(face.range, face.src).toMatch(/^U\+[0-9A-F]/);
    }
  });

  it("keeps Google's subset order, since the last face wins where ranges overlap", () => {
    for (const mono of [false, true]) {
      const subsets = faces()
        .map(subsetOf)
        .filter((face) => face.mono === mono)
        .map((face) => face.subset);
      expect(subsets, `mono: ${mono}`).toEqual(SUBSET_ORDER.filter((subset) => subsets.includes(subset)));
      expect(subsets.at(-1), `mono: ${mono}`).toBe("latin");
    }
  });

  it("gives a subset the same range in both families", () => {
    const bySubset = new Map<string, string>();
    for (const face of faces()) {
      const { subset } = subsetOf(face);
      const seen = bySubset.get(subset);
      if (seen === undefined) bySubset.set(subset, face.range);
      else expect(face.range, face.src).toBe(seen);
    }
  });

  it("preloads only the latin subsets, as subsets: ['latin'] did", () => {
    const preloaded = faces().filter((face) => face.preload).map((face) => face.src);
    expect(preloaded.sort()).toEqual(["geist-latin.woff2", "geist-mono-latin.woff2"]);
  });

  it("declares the variables globals.css maps, with their fallbacks", () => {
    const css = read(path.join(FONTS, "geist.css"));
    expect(css).toContain('--font-geist-sans: "Geist", "Geist Fallback";');
    expect(css).toContain('--font-geist-mono: "Geist Mono", "Geist Mono Fallback";');
    expect(css).toContain('font-family: "Geist Fallback";');
    expect(css).toContain('font-family: "Geist Mono Fallback";');
  });

  it("ships the font licence beside the files", () => {
    expect(read(path.join(FONTS, "OFL.txt"))).toContain("SIL OPEN FONT LICENSE Version 1.1");
  });
});

/**
 * The marketing site deploys from site/ on its own, so it serves copies of the
 * app's latin files rather than importing them (#716). Its Geist Mono was once
 * a static Regular declared for weights 100 to 900, so no weight-600 mono label
 * got a real SemiBold: Chromium drew them at Regular weight.
 */
const SITE = path.resolve(APP, "../../../site");
const SITE_FONTS = path.join(SITE, "public", "fonts");

/** Each @font-face in site.css, in declaration order. */
function siteFaces(): { family: string; weight: string; url: string }[] {
  const css = read(path.join(SITE, "src", "styles", "site.css"));
  return [...css.matchAll(/@font-face\s*\{([^}]*)\}/g)].map(([, body]) => ({
    family: /font-family:\s*"([^"]+)"/.exec(body)?.[1] ?? "",
    weight: /font-weight:\s*([^;]+);/.exec(body)?.[1].trim() ?? "",
    url: /url\("([^"]+)"\)/.exec(body)?.[1] ?? "",
  }));
}

describe("the marketing site's fonts", () => {
  it("serves the app's two latin files, byte for byte", () => {
    const files = readdirSync(SITE_FONTS).filter((name) => name.endsWith(".woff2"));
    expect([...files].sort()).toEqual(["geist-latin.woff2", "geist-mono-latin.woff2"]);
    for (const name of files) {
      const copy = readFileSync(path.join(SITE_FONTS, name));
      expect(copy.equals(readFileSync(path.join(FONTS, name))), name).toBe(true);
    }
  });

  it("declares each file once, across the variable font's weights", () => {
    expect(siteFaces()).toEqual([
      { family: "Geist", weight: "100 900", url: "/fonts/geist-latin.woff2" },
      { family: "Geist Mono", weight: "100 900", url: "/fonts/geist-mono-latin.woff2" },
    ]);
  });

  it("preloads only a file that site.css declares", () => {
    const layout = read(path.join(SITE, "src", "layouts", "Base.astro"));
    const preloads = [...layout.matchAll(/<link rel="preload" href="([^"]+)" as="font"/g)].map(
      ([, href]) => href,
    );
    expect(preloads).toEqual(["/fonts/geist-latin.woff2"]);
    expect(siteFaces().map((face) => face.url)).toContain(preloads[0]);
  });

  it("serves the font licence beside the files", () => {
    expect(read(path.join(SITE_FONTS, "OFL.txt"))).toBe(read(path.join(FONTS, "OFL.txt")));
  });
});

/** App-only style overlays and their identifying edges; never applied to the site. */
import { colour } from "./contrast-colour.mjs";

export function stylePairings(style, dark = false) {
  const facet = dark ? "classic-outer-highlight" : "classic-outer-dark";
  const surfaces = [
    ["page", ["surface-page"]],
    ["card", ["surface-page", "surface-card"]],
    ["inset", ["surface-page", "surface-card", "surface-inset"]],
    ["float", ["surface-float"]],
    ["rail", ["rail-bg"]],
    ["control", ["control-bg"]],
  ];
  return surfaces.flatMap(([surface, bg]) => [
    { label: `${style} identifying edge vs ${surface}`,
      fg: style === "classic" ? facet : "control-border", bg, min: 3 },
    { label: `${style} exterior focus vs ${surface}`, fg: "style-focus-edge", bg, min: 3 },
  ]);
}

/** Merge root → theme → palette → style, keeping light overrides out of dark. */
export function addStyleThemes(themes, css, parseBlock) {
  const styles = Array.from(
    css.matchAll(/^html\[data-style="([\w-]+)"\]:not\(\.dark\)\s*\{/gm),
    (match) => match[1],
  );
  for (const required of ["classic", "depth"]) {
    if (!styles.includes(required)) throw new Error(`missing ${required} style tokens`);
  }
  const appThemes = themes.filter((theme) => theme.floats);
  for (const style of styles) {
    const shared = parseBlock(css, `html[data-style="${style}"]`);
    const light = parseBlock(css, `html[data-style="${style}"]:not(.dark)`);
    const dark = parseBlock(css, `html[data-style="${style}"].dark`);
    for (const theme of appThemes) {
      const overlay = theme.name.endsWith("dark") ? dark : light;
      const tokens = { ...theme.tokens, ...shared, ...overlay };
      if (style === "classic") {
        for (const name of ["classic-outer-highlight", "classic-inner-highlight", "classic-outer-dark",
          "classic-inner-dark", "style-focus-edge"]) colour(name, tokens, theme.base);
      }
      themes.push({
        ...theme,
        name: `${theme.name}-style-${style}`,
        tokens,
        pairings: [...theme.pairings, ...stylePairings(style, theme.name.endsWith("dark"))],
      });
    }
  }
}

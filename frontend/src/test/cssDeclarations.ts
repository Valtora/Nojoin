/**
 * A small CSS reader for tests that compare our stylesheets with a library's:
 * enough for flat rules, groups, attribute selectors and rules nested in
 * at-rules such as @media and @keyframes. Not a general CSS parser.
 */
export type CssDeclaration = {
  /** One selector of a group, with whitespace and attribute quotes normalised. */
  selector: string;
  property: string;
  value: string;
  /** The enclosing at-rules, outermost first, or "" at the top level. */
  atRules: string;
  /** The rule's position in the sheet, for checks on source order. */
  order: number;
};

/** Splits on a separator outside parentheses, so `:not(a, b)` stays whole. */
function splitTopLevel(text: string, separator: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = "";
  for (const char of text) {
    if (char === "(") depth += 1;
    if (char === ")") depth -= 1;
    if (char === separator && depth === 0) {
      parts.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  parts.push(current);
  return parts.map((part) => part.trim()).filter(Boolean);
}

export const normaliseSelector = (selector: string) =>
  selector
    .replace(/\s+/g, " ")
    .replace(/(\[[^\]=]+[\^$*|~]?=)["']([^"']*)["']\]/g, "$1$2]")
    .trim();

/** Every declaration in the sheet, one per selector of a group. */
export function cssDeclarations(css: string): CssDeclaration[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const found: CssDeclaration[] = [];
  const preludes: string[] = [];
  let buffer = "";
  let order = 0;
  for (const char of text) {
    if (char === "{") {
      preludes.push(buffer.trim());
      buffer = "";
    } else if (char === "}") {
      const prelude = preludes.pop() ?? "";
      if (prelude && !prelude.startsWith("@")) {
        const atRules = preludes.filter((outer) => outer.startsWith("@")).join(" ");
        for (const declaration of splitTopLevel(buffer, ";")) {
          const colon = declaration.indexOf(":");
          if (colon < 0) continue;
          const property = declaration.slice(0, colon).trim().toLowerCase();
          const value = declaration.slice(colon + 1).trim();
          for (const selector of splitTopLevel(prelude, ",")) {
            found.push({ selector: normaliseSelector(selector), property, value, atRules, order });
          }
        }
        order += 1;
      }
      buffer = "";
    } else if (char === ";" && preludes.length === 0) {
      // A top-level statement such as @import, which has no block.
      buffer = "";
    } else {
      buffer += char;
    }
  }
  return found;
}

/**
 * Makes every design token on the site answer to both of the site's theme
 * inputs, at build time, from the files that already hold the values.
 *
 * The site has two theme inputs, not one: the OS `prefers-color-scheme`, and
 * an explicit choice the header toggle stores and `Base.astro` stamps on the
 * root element as `data-theme` before the first paint. A token family is only
 * correct if it answers to both, and the two combinations that matter are the
 * ones where they DISAGREE -- light chosen on a machine set to dark, and dark
 * chosen on a machine set to light.
 *
 * Both token files therefore emit the same pair of rules:
 *
 *   @media (prefers-color-scheme: dark) {
 *     :root:not([data-theme="light"]) { ...dark declarations... }
 *   }
 *   :root[data-theme="dark"] { ...dark declarations... }
 *
 * The `:not()` lets an explicit light choice win on a machine set to dark; the
 * light values already live in the unqualified `:root`, so choosing light only
 * has to stop the dark rules applying. The second rule lets an explicit dark
 * choice win on a machine set to light. Both selectors are specificity (0,2,0)
 * and carry identical declarations, so the order they resolve in never matters.
 *
 * They arrive at that pair from different starting shapes:
 *
 *   frontend/src/app/tokens.css     a top-level `.dark { ... }` block, because
 *                                   the app toggles dark mode with a class.
 *                                   The block stays in the output and is
 *                                   inert: nothing on the site carries that
 *                                   class.
 *
 *   site/src/styles/site-tokens.css a `:root` inside a `prefers-color-scheme:
 *                                   dark` media query. That selector is
 *                                   rewritten in the output rather than
 *                                   appended to, because a bare `:root` there
 *                                   ignores `data-theme` entirely.
 *
 * Doing this here rather than by hand is the point: a hand-written copy of the
 * dark declarations is a second source of truth that drifts silently, and no
 * gate compares the two. Note also that the ON-DISK shape of both files is
 * load bearing for frontend/scripts/check-contrast.mjs, which parses them as
 * two of its four themes. This transform never writes to disk, so the gate
 * keeps reading the files it expects; see the comment at the head of each.
 *
 * site-tokens.css only reaches this hook because Base.astro imports it as its
 * own module. It used to be `@import`ed from site.css, which meant Tailwind's
 * CSS pipeline inlined it and Vite never offered it to a transform -- so its
 * dark values ignored the theme toggle, and the quick-start code block
 * rendered light-grey syntax on a light surface for anyone who chose light on
 * a machine set to dark. Do not move it back behind an `@import`.
 */

const APP_TOKENS = "frontend/src/app/tokens.css";
const SITE_TOKENS = "site/src/styles/site-tokens.css";
const SITE_STYLESHEET = "site/src/styles/site.css";

/** Index of the `}` closing the block whose `{` is at `open`. */
function matchingBrace(code, open, file) {
  let depth = 0;
  for (let i = open; i < code.length; i += 1) {
    if (code[i] === "{") depth += 1;
    else if (code[i] === "}") {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  throw new Error(`tokens-theme: unbalanced braces in ${file}`);
}

const darkRules = (body) =>
  `@media (prefers-color-scheme: dark) {\n` +
  `  :root:not([data-theme="light"]) {${body}}\n` +
  `}\n` +
  `:root[data-theme="dark"] {${body}}\n`;

/** `.dark { ... }` at the top level becomes the two rules, appended. */
function transformAppTokens(code) {
  const anchored = /^\.dark\s*\{/m.exec(code);
  if (anchored === null) {
    throw new Error(`tokens-theme: no top-level .dark block in ${APP_TOKENS}`);
  }
  const open = code.indexOf("{", anchored.index);
  const end = matchingBrace(code, open, APP_TOKENS);
  return `${code}\n${darkRules(code.slice(open + 1, end))}`;
}

/**
 * The `:root` inside the dark media query is narrowed in place, then the
 * explicit-dark rule is appended. Appending alone would not be enough: the
 * media block's bare `:root` would still win on a machine set to dark even
 * when the visitor has chosen light, which is the defect this exists to fix.
 */
function transformSiteTokens(code) {
  const media = /^@media\s*\(\s*prefers-color-scheme:\s*dark\s*\)\s*\{/m.exec(code);
  if (media === null) {
    throw new Error(`tokens-theme: no top-level dark media query in ${SITE_TOKENS}`);
  }
  const mediaOpen = code.indexOf("{", media.index);
  const mediaEnd = matchingBrace(code, mediaOpen, SITE_TOKENS);

  const rootPattern = /:root\s*\{/g;
  rootPattern.lastIndex = mediaOpen;
  const root = rootPattern.exec(code);
  if (root === null || root.index > mediaEnd) {
    throw new Error(`tokens-theme: no :root inside the dark media query in ${SITE_TOKENS}`);
  }

  const open = code.indexOf("{", root.index);
  const end = matchingBrace(code, open, SITE_TOKENS);
  const body = code.slice(open + 1, end);

  const narrowed =
    code.slice(0, root.index) + ':root:not([data-theme="light"]) {' + code.slice(open + 1);

  return `${narrowed}\n:root[data-theme="dark"] {${body}}\n`;
}

export default function tokensTheme() {
  return {
    name: "tokens-theme",
    enforce: "pre",
    transform(code, id) {
      const path = id.replace(/\\/g, "/");
      if (path.endsWith(APP_TOKENS)) return transformAppTokens(code);
      if (path.endsWith(SITE_TOKENS)) return transformSiteTokens(code);

      // The regression guard. Putting site-tokens.css back behind an @import
      // here would be silent: Tailwind's pipeline inlines it, this plugin never
      // sees it, its dark values stop answering to the toggle, and every gate
      // still passes. Fail the build instead.
      if (path.endsWith(SITE_STYLESHEET) && /@import\s+["'][^"']*site-tokens\.css["']/.test(code)) {
        throw new Error(
          "tokens-theme: site.css @imports site-tokens.css. That hides it from this " +
            "plugin, so its dark values would ignore the header toggle. Import it as a " +
            "module in Base.astro instead.",
        );
      }
      return undefined;
    },
  };
}

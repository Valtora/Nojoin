/**
 * Maps the app's class-based dark theme onto the site's media-query dark
 * theme, at build time, from the same source file.
 *
 * The app toggles dark mode with a `.dark` class because a user can override
 * their OS preference in settings. The site follows `prefers-color-scheme` by
 * default and lets a visitor override it with the header toggle, which stores a
 * choice and stamps `data-theme` on the root element. Rather than keeping a
 * hand-written copy of the dark values, which would drift, this transform reads
 * the `.dark { ... }` block out of tokens.css as Vite loads it and appends two
 * rules built from the same declarations:
 *
 *   @media (prefers-color-scheme: dark) {
 *     :root:not([data-theme="light"]) { ...same declarations... }
 *   }
 *   :root[data-theme="dark"] { ...same declarations... }
 *
 * The `:not()` is what makes an explicit light choice win on a machine set to
 * dark; the light values already live in the unqualified `:root`, so choosing
 * light only has to stop the dark rules applying.
 *
 * The `.dark` selector block itself stays in the output and is inert: nothing
 * on the site ever carries that class.
 */
export default function appTokensMedia() {
  return {
    name: "app-tokens-media",
    enforce: "pre",
    transform(code, id) {
      if (!id.replace(/\\/g, "/").endsWith("frontend/src/app/tokens.css")) return;

      const anchored = /^\.dark\s*\{/m.exec(code);
      if (anchored === null) {
        throw new Error("app-tokens-media: no top-level .dark block in tokens.css");
      }
      const open = code.indexOf("{", anchored.index);
      let depth = 0;
      let end = open;
      for (let i = open; i < code.length; i += 1) {
        if (code[i] === "{") depth += 1;
        else if (code[i] === "}") {
          depth -= 1;
          if (depth === 0) {
            end = i;
            break;
          }
        }
      }
      const darkBody = code.slice(open + 1, end);
      return (
        `${code}\n` +
        `@media (prefers-color-scheme: dark) {\n` +
        `  :root:not([data-theme="light"]) {${darkBody}}\n` +
        `}\n` +
        `:root[data-theme="dark"] {${darkBody}}\n`
      );
    },
  };
}

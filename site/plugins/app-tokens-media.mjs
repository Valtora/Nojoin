/**
 * Maps the app's class-based dark theme onto the site's media-query dark
 * theme, at build time, from the same source file.
 *
 * The app toggles dark mode with a `.dark` class because a user can override
 * their OS preference in settings. The site has no JavaScript, so it follows
 * `prefers-color-scheme` instead. Rather than keeping a hand-written copy of
 * the dark values under a media query, which would drift, this transform reads
 * the `.dark { ... }` block out of tokens.css as Vite loads it and appends
 *
 *   @media (prefers-color-scheme: dark) { :root { ...same declarations... } }
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
      return `${code}\n@media (prefers-color-scheme: dark) {\n  :root {${darkBody}}\n}\n`;
    },
  };
}

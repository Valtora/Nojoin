/**
 * The /docs URL contract, as code.
 *
 * The plan's first implementation was a static _redirects file, and local
 * wrangler dev honoured it in full. Deployed Workers assets did not: a
 * placeholder followed by a literal suffix (`/docs/:page.html`) never
 * matches in production, so `.html` requests fell through to the generic
 * rule and redirected to `PAGE.html.md`, and the trailing-slash rule for
 * `/docs/` did not match at all. This Worker owns the mapping instead.
 *
 * `run_worker_first` in wrangler.jsonc limits execution to `/docs` and
 * `/docs/*`: every other path is served straight from static assets and
 * nothing executes per request, which preserves the assets-only property
 * for the site itself. Fragments need no handling here; a fragment is never
 * sent to the server, and the browser reattaches it to the redirect target.
 */

const DOCS = "https://github.com/Valtora/Nojoin/blob/main/docs/";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === "/docs" || path === "/docs/") {
      return Response.redirect(`${DOCS}README.md`, 301);
    }

    if (path.startsWith("/docs/")) {
      const page = path
        .slice("/docs/".length)
        .replace(/\/+$/, "")
        .replace(/\.(html|md)$/i, "");
      return Response.redirect(`${DOCS}${page}.md`, 301);
    }

    // Unreachable while run_worker_first covers only /docs*, but correct if
    // that list ever widens: everything else is the static site.
    return env.ASSETS.fetch(request);
  },
};

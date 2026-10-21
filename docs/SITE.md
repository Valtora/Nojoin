# The Marketing Site

`www.nojoin.co.uk` is an Astro static build in `site/`, served by a Cloudflare Worker with static
assets. This document covers the site's composition, copy, claims and maintenance.
[DESIGN.md](DESIGN.md#the-marketing-site) is the authority on tokens, theming and the contrast
gate.

## What the site is

| Route | Source | Content |
| --- | --- | --- |
| `/` | `site/src/pages/index.astro` | Landing page |
| `/compare/` | `site/src/pages/compare.astro` | Comparison with Jamie, Otter and Granola, from `site/src/data/comparison.ts` |
| `/managed/` | `site/src/pages/managed.astro` | The managed service |
| any other path | `site/src/pages/404.astro` | Not found |

Every page uses `site/src/layouts/Base.astro`, with `Header`, `Footer`, `Shot` (a framed
screenshot) and `Mark` (the logo, inlined from `assets/images/nojoin-mark.svg`) in
`site/src/components/`.

Documentation stays on GitHub and the site links to it. `/docs` redirects (301) to
`docs/README.md` on GitHub and `/docs/<PAGE>` to `docs/<PAGE>.md`, with any trailing `.html` or
`.md` dropped. Released images link to `www.nojoin.co.uk/docs/TELEMETRY`
(`frontend/src/components/TelemetryNotice.tsx`), so these redirects must stay.

Both themes follow `prefers-color-scheme`. A header toggle stores an override in `localStorage`
under `nojoin-theme`, applied before first paint, and the screenshots follow the active theme.
There is no framework and no hydration: `Base.astro` carries two small inline scripts plus the
Cloudflare beacon. Fonts (Geist and Geist Mono) are self-hosted in `site/public/fonts/`; a page
arguing that nothing leaves your server does not fetch fonts from a CDN.

## Design direction

Change these only by the site owner's decision.

- **Hero.** A centred stack: headline, lede, two CTAs (Get started, Star on GitHub), then the
  full-app dashboard screenshot.
- **Headline.** "Open-Source Meeting Notes on a Server You Own". Above 48rem it breaks before
  "on", and the Open Graph card breaks in the same place. Changing it means regenerating the
  card (see [The Open Graph card](#the-open-graph-card)).
- **Type scale.** Hero `clamp(2.75rem, 6.5vw, 4rem)`, section headings 2.25rem, Geist semibold
  (600) with -0.025em display tracking. No other weight.
- **Screenshot frame.** Every screenshot sits in `Shot.astro`'s browser-chrome frame: three dots,
  a `nojoin.your-server.net` URL pill, an orange edge, 14px radius and `--lift-strong`.
- **Agent band.** Directly under the hero, on the hero's wash, with no screenshot: the assistant
  works in Claude's or ChatGPT's window, and showing that inside a frame reading
  `nojoin.your-server.net` would be a false image. Three devices carry it instead:
  - a flow card walking one post-meeting job, each step tagged with its actor ("Agent",
    "Nojoin", "You"; never a vendor name);
  - a six-card tool showcase giving a count per group, never a list of tools;
  - a wider bridge card for the CRM claim.

  The prose names Claude and ChatGPT. The tool counts ("thirty-two tools, nineteen of which
  write", and the six group counts) are copied by hand from [MCP.md](MCP.md#available-tools)
  into `index.astro` and into the `agents` rows of `comparison.ts`. Re-count both whenever a
  tool is added or removed.
- **Feature rows.** Meeting Edge, Transcripts and Notes put the copy beside the shot, alternating
  sides. This works only because each shot is cropped to its row's subject; if a shot goes back
  to being a whole window, fix the crop, not the grid. A row whose screenshot shows nothing new
  is cut.
- **One surface, no hard bands.** `.band` carries only `--band-wash`, and depth comes from the
  frames and cards floating on the page. `.band` and `.section` must strictly alternate down
  every page, because two adjacent bands are indistinguishable. Check this whenever a section is
  added or removed.
- **The quick-start code block** is dark in both themes and ignores the toggle.
- **Colour.** Neutral, plus one orange-700 closer band (`.closer`) at the foot of the page. No
  cream tints and no glass.
- **Closers.** The landing page ends on the managed-service teaser, with no repeat of the hero's
  CTAs. `/managed/` ends on its email CTA.
- **No three-up strip and no privacy section on the landing page.** Both restated the hero.
  `/managed/` keeps its own strip.
- **Site-only effects.** The site departs from the app's flat canon only in the ways DESIGN.md
  lists, all declared in `site-tokens.css`: the primary button's glow (`--glow-action`,
  `--glow-inset`), the hero wash (`--hero-wash`), the band wash (`--band-wash`), card lift
  (`--lift`, `--lift-strong`) and the frame edge (`--frame-accent`). Add no others.
- **Nav.** Home, Quick start (`/#quick-start`, absolute because the section exists only on the
  landing page), Pricing (`/managed/`), Compare, Docs (`docs/README.md` on GitHub), then the
  star badge and the theme toggle. There is no menu button: the header wraps to a second row
  down to 320px. Revisit only if the nav stops fitting.
- **Sticky header** with a solid fill and no `backdrop-filter`. `section[id]` reserves its
  height with `scroll-margin-top`: 8.5rem below 48rem, where the nav wraps, and 5rem above.
- **Selective highlight.** At most one `<mark class="hl">` per page, on a flat `--action-tint`.
  A page with nothing that earns it gets none.

### Screenshots

- **Crop to the row's subject**, about 540 to 800 logical pixels wide, so it renders near life
  size in half the wrap. Only the hero shows the whole app.
- **Capture at the width the crop needs** instead of cropping a wide window, so lines wrap
  inside the frame.
- **No two shots show the same content.**
- **Pairs.** Each shot is `site/public/images/<name>-light.webp` and `<name>-dark.webp`,
  captured from one clip in both themes so the pair shares a ratio. Pass the file's dimensions
  to `Shot` as `width` and `height`; the defaults (1920 by 1080) suit only the 16:9 hero.
- **Delete unreferenced captures** from `site/public/images/`.
- **Sources.** The dashboard, Meeting Edge and notes shots come from a seeded demonstration
  instance of public-domain recordings processed by the real pipeline, with speakers named as
  the people they are; the Meeting Edge and notes shots use the Artemis briefing. The
  transcripts shot needs several speakers in a short span, so it uses "Welcome to Nojoin", the
  sample meeting every install seeds: a scripted transcript with four made-up speakers. Any
  user can recreate it under **Settings > Help > Demo meeting > Create it again**, or with
  `POST /api/v1/system/seed-demo`.

### Tokens

`Base.astro` imports `frontend/src/app/tokens.css` and `site/src/styles/site-tokens.css` as
modules, so `site/plugins/tokens-theme.mjs` can make their dark values answer to the toggle.
Never `@import` `site-tokens.css` from `site.css`: the plugin fails the build if you do.
[DESIGN.md](DESIGN.md#the-marketing-site) covers the rest.

## Voice

Plain, direct, a little opinionated, second person, British English, no emoji. Nojoin is a
product, so marketing copy never says "I" or "we".

**`/managed/` is the exception.** It sells one person's time, so it writes in the first person
singular and says so in its first paragraph. It names nobody: he is the person who built Nojoin,
and no name goes on the site or into the repository. The landing page's teaser for it stays in
the product's voice and says "Nojoin's developer". "We" is banned everywhere, including
`/managed/`.

- **Title case for headings and eyebrow labels**, in the conventional style: short function
  words stay lower case unless they open or close the line ("Every Word, Attributed to the
  Right Person", "£24.99 a Person, a Month"). Body copy, leads, buttons, table cells and alt
  text use sentence case.
- **Contract**: "it's", "can't", "won't".
- **Connect the claims.** Carry the reader from one fact to the next with "so", "because" or
  "when". Avoid unrelated facts joined by ", and" for balance, and runs of short declaratives.
- **Ration the negatives.** "No bot joins the call" is the one negative the site leads with.
  Elsewhere, say what Nojoin does: "how much you record is up to your hardware", not "nothing
  is metered". The comparison page's opening line and the limits on `/managed/` are exempt.
- **No punchline closers.** A paragraph ends on its last fact.
- **No em dashes or en dashes.** Use a comma, a colon or a full stop. Page titles separate with
  a pipe.
- **Numbers instead of adjectives**, wherever a true number exists.
- **Name the actor.** No bare "it" when two subjects are in play.
- **Don't guess at the reader.** No "you probably", no assumptions about their setup or size.
- **Concede facts.** Where a competitor does something well, say so.
- **Sell with checkable facts.** The register is confident, but a number survives being checked
  and a superlative does not.
- **Plain labels.** An evocative label is fine only if its meaning is obvious in half a second.
- **Whole jobs.** Every feature example ends on something delivered: the transcript attributed,
  the notes written, the task filed.
- **Say each idea once**, on the page where it lands hardest. When a claim appears twice,
  delete the weaker instance. The landing page argues one thing, open-source meeting notes on
  hardware you own that an assistant can edit, and each section below the hero is evidence for
  it.
- **Word budget.** About 1,000 words at most across the three pages, counting everything inside
  `<main>` except the comparison tables and the code block. There is no floor.

**Banned everywhere**: seamless, powerful, robust, enterprise-grade, best-in-class,
cutting-edge, unlock, empower, leverage, revolutionise, game-changing, AI-powered,
frictionless, turnkey, peace of mind, bank-grade, and any superlative claiming exclusivity
(only, first, unique, unmatched).

## Claims and the comparison page

Every Nojoin claim on the site traces to `README.md` or a file in `docs/`. Nothing is invented
for marketing effect.

`comparison.ts` holds two tables: `rows`, the detailed comparison (one text cell per product),
and `summaryRows`, the at-a-glance table (a `yes`, `partial` or `no` verdict and a short note per
cell). Columns run Nojoin, Jamie, Otter, Granola; Jamie sits second because it is the closest
rival and column order decides which competitor gets read. Which competitors appear is the site
owner's call. Their names also appear in the landing page's comparison band and in
`compare.astro`'s description and heading.

The page shows no source URLs or read dates. The standard behind each cell still holds:

- **Check every competitor claim against that vendor's own current documentation** before it
  goes up. Not memory, a review site, or a competitor's comparison page.
- **An unverifiable claim stays off the page.** Write "Not stated in current docs" (detailed
  table) or "Not stated in docs" (summary): it says what was looked for and not found, never that
  a feature is missing. This applies when a vendor's page cannot be opened, too. A row whose
  point rests on such cells does not go up at all.
- **No competitor pricing.** Plan limits are fine.
- **Don't demote a competitor on an assumption.** Argue from what Nojoin documents.
- **Nothing checks for staleness.** Re-check a row by hand when its vendor announces a change.
- **Choose summary rows fairly.** A summary that lifts only the axes Nojoin wins always sweeps,
  so count the axes left out as well as the verdicts. One argument gets one row: self-hosting,
  processing and storage are a single row, as are licence and model choice, while the detailed
  table keeps them separate. Nojoin currently takes a yes on every summary row. That is
  acceptable only because each concession is a tie (Jamie and Granola on no-bot capture, Jamie
  and Otter on speaker memory, Otter on portability). Never add a Nojoin cross to look
  even-handed or remove a competitor's tick to look ahead.
- **Keep the speaker rows as a pair.** "Speakers remembered between meetings" concedes Jamie's
  and Otter's yes, so "Voice models you hold and can rebuild" (argued from
  [USAGE.md](USAGE.md)) reads as the specific claim it is. Alone, the second row implies the
  others forget speakers, and Otter's documentation contradicts that.
- **Live in-meeting guidance stays out of the summary**, because Otter and Granola document a
  form of it.

## The managed service

`/managed/` sells the developer of Nojoin scoping, installing and maintaining a private instance
for an organisation, on hardware the organisation owns. Its tagline is "Nojoin on Your Hardware,
Run by Its Developer". The terms below are what the page commits to; keep the page and this list
in step.

- **Price.** £24.99 a person a month, minimum five people. The figure appears in `/managed/`'s
  meta description, hero and price card, and in the landing page's closer. Change all of them
  together.
- **Hardware.** The customer buys and owns it and pays the supplier directly; the fee is labour
  only. The page describes the machine (the 8 GB VRAM practical minimum from
  [DEPLOYMENT.md](DEPLOYMENT.md), with CPU-only named as slower) and never prices it.
- **No caps.** No usage meter, no limit on call length, no history that expires. The comparison
  page's "Limits on how much you process" row carries the competitor side.
- **Support.** Same day if the instance is down, next working day otherwise, UK working hours. No
  uptime figure, SLA or capacity claim.
- **Included.** Monitoring from outside the customer's network; upgrades in one maintenance
  window a month, with security fixes applied as soon as they're out; backups set up and
  test-restored.
- **Feature requests.** Managed customers' requests are read first and always answered.
  Anything built ships to everyone under AGPLv3, so a customer can stop paying and keep the
  same software.
- **Kept off the page.** The data processing agreement and the admin access maintenance needs.
  Nothing on the page claims the data is unreachable.
- **Contact.** The only call to action is `hello@nojoin.co.uk`, a Proton alias that can be
  replaced if it attracts spam. There is no booking page; the comment in `managed.astro` gives
  the reason.

## Working on the site

Build on Node 26 with the npm it bundles, as CI and the deploy job do. Check `node --version`
first: an older npm rewrites `package-lock.json` in ways CI rejects.

```bash
cd site
npm ci                   # never `npm install`
npm run build            # writes site/dist
npm run serve            # builds, then serves the build on port 4322
```

`npm run dev` exists, but review the built output: the dev server resolves imports differently.

### Sharing a preview

A Cloudflare quick tunnel shares a preview with someone who cannot reach your ports, with no
account, DNS record or token:

```bash
cloudflared tunnel --url http://127.0.0.1:4322
```

It prints a temporary `https://<random>.trycloudflare.com` URL. `astro.config.mjs` allows
`.trycloudflare.com` under `vite.preview.allowedHosts`; without it the preview server rejects
tunnelled requests.

**If the host also runs a `cloudflared` service for live traffic, never stop, restart or
reconfigure it.** `pkill -f cloudflared` kills both, and the pattern can match the invoking
shell too. Stop preview tunnels by PID when the review is done.

### Page titles

`Base.astro` takes `title` for the browser tab and an optional `ogTitle` for the share card,
defaulting to `title`. The landing page's tab is `Nojoin`; sub-pages use `Subject | Nojoin`
(`Compare | Nojoin`, `Managed | Nojoin`). Set `ogTitle` only where the card needs more words
than the tab: today the landing page ("Nojoin: open-source meeting notes on a server you own")
and `/managed/`. Keep the two props separate, or every share card shows the bare tab title.

### The Open Graph card

`site/public/images/og-card.png` is the one card every page shares, and it carries the hero
headline. It is also the image at the top of `README.md` and, uploaded by hand in the repository
settings, the GitHub social preview. When the headline changes:

1. Edit `HEADLINE` in `site/scripts/build-og-card.mjs`, one array entry per line to match the
   page's break, and run `node site/scripts/build-og-card.mjs`. Playwright is not a dependency
   of this repository: `playwright-core` must resolve from a directory outside it, and
   `PLAYWRIGHT_CHROMIUM` points at a Chromium binary.
2. Update `og:image:alt` in `Base.astro` and the image's `alt` in `README.md`.
3. Upload the new card as the social preview.

The card's colours are the app's dark-theme tokens, copied into the script. Nothing checks them,
so update the script when those tokens change.

### What a local preview cannot show

The `/docs` redirects live in `site/worker/index.js`, which runs only on the deployed Worker, so
`/docs/TELEMETRY` returns 404 under `npm run serve`. There is no staging copy (`workers_dev` is
off), so after changing the Worker or `wrangler.jsonc`, check
`https://www.nojoin.co.uk/docs/TELEMETRY` once the deploy finishes.

### Checks before review

- **CI.** The `Site build` job in `ci.yml` runs `npm ci`, `npm run build` and
  `node frontend/scripts/check-contrast.mjs` when `site/`, `frontend/src/app/tokens.css`, the
  contrast script, or the Docker, compose, nginx or CI files change. Run the same locally.
- **Contrast pairings.** The gate audits the site as two of its four themes, but only the
  pairings in `SITE_PAIRINGS`. Register any new colour-on-colour element there in the same
  change. It measures values, not whether a rule applies; DESIGN.md explains the blind spots.
- **The four-way theme matrix.** The OS setting and the stored choice are independent inputs,
  and the bugs live where they disagree. Force each one separately (Playwright's `colorScheme`
  context option for the OS, an init script setting `nojoin-theme` for the choice):

  | Stored choice | OS setting | What must render |
  | --- | --- | --- |
  | none | light | light |
  | none | dark | dark |
  | light | dark | light |
  | dark | light | dark |

  Read the computed value of a site-only token that changes with the theme, such as
  `--frame-chrome`, rather than judging by eye: the app tokens can look right while the
  site-only ones are wrong.
- **Horizontal overflow** at 360px and 1920px, in both themes, on every page: compare
  `document.documentElement.scrollWidth` with `window.innerWidth`. An element scrolling inside
  its own `overflow-x: auto` box (the code block, the comparison tables) is fine; the document
  scrolling is not.
- `python3 scripts/validate_docs.py` and `git diff --check`.

## Deployment

`.github/workflows/site-deploy.yml` builds and deploys whatever `main` holds, on four triggers: a
push to `main` touching `site/**`, a published release, a weekly schedule (Monday 06:12 UTC) and
manual dispatch. It runs `wrangler deploy` with the `CLOUDFLARE_API_TOKEN` secret, one deploy at
a time.

`site/wrangler.jsonc` defines the `nojoin-site` Worker:

- **A Route**, `www.nojoin.co.uk/*` on the `nojoin.co.uk` zone, in front of the existing proxied
  DNS record. A Custom Domain would have meant deleting that record first.
- **`workers_dev: false`**, so there is no second public copy of the site.
- **Static assets** from `./dist`, with `404-page` handling. `run_worker_first` sends only
  `/docs` and `/docs/*` to the Worker script.

**Roll a bad deploy forward.** Reverting an ordinary `site/` commit is fine, because the next
deploy publishes whatever `main` holds. Never revert the commit that added the route, and never
delete the route in the Cloudflare dashboard: nothing else serves the hostname, so the site goes
down. A deleted route also comes back with the next scheduled deploy.

**Build-time data.** `site/src/data/github.ts` fetches the star count using CI's `GITHUB_TOKEN`;
if the API fails, the badge shows no number and the build carries on. The footer version comes
from `docs/VERSION` in the checkout.

**Analytics.** Cloudflare Web Analytics reads `PUBLIC_CF_BEACON_TOKEN` at build time, supplied
from the repository variable `CF_BEACON_TOKEN` (a variable, not a secret). It is the beacon
`siteTag`, which grants nothing and is public in every page's markup. **Never put a Cloudflare
API token there**: that is a real credential. Local builds leave it unset, so they render no
beacon and neither they nor preview tunnels reach the live figures.

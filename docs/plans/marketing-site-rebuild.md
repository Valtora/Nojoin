# Plan: rebuild nojoin.co.uk as an Astro site on Cloudflare

Working document for the marketing site rebuild. It is deleted in the final commit of the
work it describes, per repository convention. Nothing here is durable documentation:
anything that outlives the work belongs in `docs/DESIGN.md`, `CONTRIBUTING.md` or
`docs/DEVELOPMENT.md` instead.

## Objective

Replace the Jekyll site served from GitHub Pages at `www.nojoin.co.uk` with a two-page
marketing site: Astro plus Tailwind v4, built in CI, deployed to Cloudflare Workers static
assets. The positioning is Nojoin as the open-source alternative to commercial meeting
transcription SaaS, so the site has to read as a product site rather than as a rendered
README.

The Jekyll site exists to render Markdown. That is what makes it fragile: its stylesheet
carries selectors such as `h2#quick-start + ol > li::before` that depend on the shape of
`README.md`, so an edit to the README can break the site's layout. The rebuild removes the
coupling by not rendering Markdown at all.

## Settled decisions

Agreed before implementation. Reasons recorded so they are not revisited by accident.

### Scope

| Decision | Reason |
| --- | --- |
| Three pages: landing, comparison and managed | No docs platform to build or maintain. The third page was anticipated below and arrived sooner than expected |
| Docs stay on GitHub as `docs/*.md`, linked out to | One source of truth. The site never holds a second copy that can drift |
| `/docs/*` 301s to `github.com/Valtora/Nojoin/blob/main/docs/*` | Released images hardcode `www.nojoin.co.uk/docs/TELEMETRY`. See "The URL contract" |
| Two equal hero CTAs: Get Started, Star on GitHub | A visitor ready to install should not have to hunt for the command |
| Nav and layout accommodate a third page later | Superseded, and the reasoning held: `/managed/` was added afterwards with one nav entry, one landing band and no redesign. It publishes a monthly figure, so the original "no pricing" position no longer stands. See SITE.md, "The managed service" |

### Stack and hosting

| Decision | Reason |
| --- | --- |
| Astro with Tailwind v4 | Ships no JavaScript by default, and does not couple the site to the app's dependency upgrades the way a Next.js static export would |
| Site lives at `site/` in this repo | Same gates, same review, same release cadence |
| Cloudflare Workers static assets, deployed from GitHub Actions with wrangler | Cloudflare Pages would build outside this repo's gate set |
| `www` canonical, apex 301s to it | `www` is compiled into every released image, so making it canonical avoids a permanent extra hop for the URLs that matter |
| No staging subdomain | Review on the Worker's free `*.workers.dev` URL, then cut over |
| Cloudflare Web Analytics | Cookieless and needs no consent banner, which is defensible against the project's own privacy positioning in a way Google Analytics is not |
| Star count and release version fetched at build time | Keeps the page static. Freshness comes from a weekly scheduled rebuild and a rebuild on each published release |
| Same AA contrast bar as the app, gated in CI | The site is already in the gate today and must not fall out of it |

### Decisions taken during clarification

| Question | Decision | Consequence |
| --- | --- | --- |
| Light and dark? | Both, following `prefers-color-scheme`, with no toggle | Genuinely zero JavaScript of our own. Both screenshot sets ship and swap via `<picture>`, which downloads only the matching source. The contrast gate audits the site in two themes, not one |
| Attio in the comparison? | Dropped | It is a CRM with a notetaker attached, not a meeting transcription product. Three competitors on the same axes beats four with one column of gaps |
| Visual direction? | App tokens, marketing composition | Same tokens, type, radii and flat canon. Visual interest comes from marketing-scale composition: tall hero, full-bleed section bands, large framed screenshots, generous vertical rhythm. No gradients, no glass, no decorative shapes |
| Token sharing? | Extract a shared `tokens.css` | One source of truth rather than an enforced copy. Costs a small refactor of `globals.css` |
| Analytics beacon versus zero JS? | Accept the beacon | About 5KB of Cloudflare's script. Zero JavaScript of our own writing, which is what the no-toggle decision was actually protecting |
| Screenshot sequencing? | Demo stack early, images last | The demo instance and seed script land early so the manual audio import can start whenever suits. The landing page is built against placeholder frames and the real images drop in at the end |
| Demo stack in the repo? | Seed script yes, compose file no | The seed script is a reusable fixture. The compose file is the example with different ports, and a second compose file in the repo muddies which one is canonical |

### Decisions taken during interrogation (2026-08-02)

The plan was grilled branch by branch. These decisions supersede anything they contradict
in the sections below, and the sections have been rewritten to match.

| Question | Decision | Consequence |
| --- | --- | --- |
| How does P1 deploy from an unmerged branch? | It cannot: `workflow_dispatch` requires the workflow on the default branch, and `push` is filtered to `main`. P1 becomes its own small PR | The pipeline is proved exactly as it will run for real. Deploying a placeholder to workers.dev is harmless because no route exists yet |
| One branch through P9? | No. Four PRs: pipeline, site, cutover, teardown | The settling period no longer holds a branch open, and production only ever serves merged code |
| Route management and rollback? | Route lives in `wrangler.jsonc`; cutover is a one-line PR; rollback is reverting that commit and deploying | "Delete the route in the dashboard" is NOT the rollback path: the weekly scheduled deploy from `main` would silently re-add it |
| Local toolchain? | Node 26 via nvm (already installed and now the default; npm 11.17.0) | Matches CI and the release images. Non-interactive shells fall back to the distro Node 20 / npm 9.2.0, which is the pairing that corrupted a lockfile before — every `site/` npm or wrangler command runs in an nvm-sourced shell, and the lockfile is generated with npm 11 |
| og:image? | A dedicated 1200×630 card composed from the mark, wordmark and tagline | Eleventh image in P7. OG cards cannot theme-swap, so it is one fixed image, immune to screenshot churn |
| Comparison claim staleness? | The weekly workflow gains a source-URL check as a separate, non-blocking job | A dead source turns the run red and notifies, but the deploy job does not depend on it — a competitor outage must not veto the site's own rebuild. The check catches dead links only; changed facts still rely on the dated footnotes |
| Demo audio source? | Public-domain, meeting-shaped audio: US government hearings, city-council sessions, NASA briefings. Labelled honestly — real titles, real speaker names | Diarisation and waveforms are genuine, no privacy or licence exposure, and no invented names sit over real people's words. Seed fixtures supply the surrounding density. Provenance recorded per import |

### Decisions taken during the P4/P7 interrogation (2026-08-02)

The demo-seeding and screenshot phases were grilled once the earlier phases had merged.
Reading the code first turned up three things the plan had assumed away, and the decisions
below supersede the original P4 and P7 text, which has been rewritten to match.

| Finding or question | Decision | Consequence |
| --- | --- | --- |
| The demo stack has no LLM provider, so notes, Edge and chat cannot be genuine | Point it at an Ollama server on the LAN running `qwen3:14b`, context window 16384 | Every AI surface in the screenshots is real product output, at no API cost. `ollama_url_policy` permits a private address because every entry point passes `allow_private=True`. Embeddings are local ONNX, so search never depended on the provider. Note the context window is a *reduction* from the shipped 131072 default, which no 14B model on a 16GB card can hold a KV cache for |
| Ollama's thinking models could pollute the notes | No change needed | Ollama 0.32.5 returns reasoning in `message.thinking`; the backend reads `message.content` only, so it is discarded. Verified against the live server rather than assumed |
| Meeting Edge only renders while a recording is in flight, so it cannot be captured from a finished one | Generate a real payload from the Artemis transcript, then stage that recording back into an in-flight status for the capture | The panel renders purely from `transcript.meeting_edge_payload`, so the content is genuine and the framing is controlled. Capturing a live session instead would need tab capture and a well-timed shot |
| Only one real recording exists, but three shots need a transcript | Accepted: the transcript, Edge and chat shots all show the Artemis briefing | Each shot is individually credible, and Edge suits a press conference well. Inventing a transcript for a seeded meeting was rejected outright |
| What the seeded rows describe | A mixed small business: eight recordings over three weeks, fictional people, tags, tasks and calendar events | Broad enough not to assume the reader's trade. Calendar events are seeded independently of the recordings so the current week stays full |
| The owner account is visible in the UI | James Smith, on a reserved `.example` address | RFC 2606 guarantees the address can never resolve, and no real person's data appears |
| `/app` is baked into the image and only `./data` is mounted | `docker cp` the seed script into the api container and run it there, against the app's own session | The models and Alembic stay the single source of truth. Hand-written SQL would reproduce the test-DDL-drift failure this repository has hit before |
| The script's name collides with the application's own seeder | Named `scripts/seed_demo_instance.py`, not `seed_demo_data.py` | `backend/seed_demo.py` already defines `seed_demo_data()`, which seeds the "Welcome to Nojoin" recording every install gets. Two different things sharing a name in one repository is a reading trap. First-run setup is also driven with `include_demo_recording=false`, so the stock recording stays out of the screenshots |
| Re-running the seed script | Aborts if its own marker is present; `--reset` deletes only its own rows | Iterating on seed content is a one-command loop, and the Artemis import with its GPU time is never at risk |
| How the screenshots are taken | Playwright headless on the host, 1920x1080 at device scale factor 2, both colour schemes in one run, with the app's navigation collapsed | 1920x1080 is the desktop most visitors actually have, and the sidebar at marketing scale is a column of labels too small to read, so collapsing it hands the width to the subject of each shot. Ten shots stay pixel-consistent and are reproducible after any content change. Installed outside the repository, which has no browser-automation dependency and should not gain one for a job that runs about once a year |
| Screenshot format | WebP at 2560x1440, quality 82 | The hero displays at roughly 1072 CSS px inside the 72rem wrap, so 2560 is about 2.4x the real display size. Downscaling from the 2x capture supersamples, which both sharpens small interface text and compresses better: 1.27 MB for all eleven images. `Shot.astro` hardcoded `.svg` at 1440x900, so P7 changes the component's sources and its declared aspect ratio as well as the files |
| Open Graph card content | Mark, wordmark, the headline at display scale, and the descriptor small beneath | A social card arrives without context, so it carries both the hook and a plain statement of what Nojoin is. `Base.astro` gains `og:image` and the Twitter card tags, neither of which exists yet |
| The document behind the chat shot | The NASA Artemis II press kit | Public domain, genuinely about the audio, and it exercises the real PDF parsing path rather than a text file written for the occasion |

### Polish round after the first screenshots landed (2026-08-02)

Reviewing the real screenshots on the page produced a second round of decisions. The
durable ones are already folded into `docs/SITE.md`.

| Finding or question | Decision | Consequence |
| --- | --- | --- |
| The feature screenshots were unreadable | Feature rows stack: copy at reading width, screenshot beneath in a 102rem container | Side by side, the shot got half of a 72rem wrap and rendered a 1920px capture at about 28%. Full width it lands near 1600px on a 1920 screen, roughly 85% of life size. The alternating rhythm survives as the copy block changing sides |
| The Meeting Edge shot showed a recording being processed | Replaced with a genuine live capture | The panel is stageable from the database, but the waveform is drawn from a real MediaStream and cannot be. Chromium is given a fake microphone fed from a WAV of the briefing (`--use-file-for-fake-audio-capture`), so the waveform, the live transcript and the guidance are all real output. The typed focus line and note visibly change what Edge returns |
| Light and dark toggle | Added, superseding the no-JavaScript decision | Two small inline scripts: one stamps the stored choice on the root element before first paint, one wires the button. The build-time token transform now emits both a `prefers-color-scheme` rule and a `[data-theme]` rule from the same source. The screenshots follow the chosen theme by having their `<source media>` forced |
| A simpler comparison | An at-a-glance table above the detailed one, ticks and crosses with a short qualifier | Rows chosen because their answers are structural. Nojoin does not sweep it: Granola also records without a bot, Otter also remembers speakers between meetings. Live in-meeting guidance is deliberately absent as a row, because all four products document some form of it and claiming it as a difference would be the overclaim `docs/SITE.md` exists to prevent |
| The quick-start steps broke mid-token | The step counter is positioned rather than laid out with flex | `display: flex` on the `li` made every inline `<code>` its own flex item, so `FIRST_RUN_PASSWORD` got a column of its own and broke with room to spare. This was introduced by the 360px overflow fix, which let those items shrink |

Two 360px regressions came out of this round and were fixed: the theme toggle pushed the
header nav past the viewport, so the header wraps; and the at-a-glance table's
visually-hidden verdict text, being absolutely positioned with no positioned ancestor, was
contained by the initial containing block rather than the table's scroll container, which
made the whole document scroll sideways by the width of the table.

### Design direction (locked 2026-08-02)

Resolved by a second interrogation and a three-variant mock-up comparison built from the
app's real tokens and fonts. The mocks varied only display scale; everything else below
was decided by questioning first, so the pick is genuinely about register.

| Decision | Detail |
| --- | --- |
| Hero | Centred stack: headline, subhead, two CTAs, full-width dashboard screenshot below |
| Screenshot framing | Minimal browser chrome on every shot: dots, a muted `nojoin.your-server.net` URL pill, hairline border, 12px radius, the 4% resting shadow in light only |
| Feature rows | Text and screenshot side by side, alternating sides down the page, on the alternating page/card bands |
| Colour weight | Neutral bands throughout, plus one solid orange-700 full-bleed closer band at the foot (white text, 5.18:1); no cream tints, no other colour moments |
| Headline | No-bot led: "Meeting notes, with no bot in the call". Self-hosting lands in the subhead |
| Display scale | **Variant C, "Statement": 64px hero** — `clamp(2.75rem, 6.5vw, 4rem)`, section headings 2.25rem, hero padding ~6.75rem, section padding ~5.5rem, subhead 1.25rem. Geist stays semibold (600) with -0.025em tracking; no new weight enters the system |
| Positioning strip | Three columns: mic / globe / server Lucide icons in accent, 1.0625rem headings |
| Closer CTAs | White-fill button with orange-700 label, plus a white-outline ghost |

### Voice and copy, imported from the Vorkane brand docs (2026-08-02)

The durable record of the site's brand, voice and maintenance rules now lives in
`docs/SITE.md`, which survives this plan's deletion. The summary below records what was
decided during this work and why; where the two disagree, `docs/SITE.md` wins.

Vorkane, the sibling brand, carries a mature set of writing rules in its own private brand
documents (voice, messaging, decisions, brand). The two brands are siblings, not twins:
these are the rules that transfer, the ones that do not, and the devices adopted.

**Transfers, applied to every page of copy:**

- **Contract everything.** "It's", "can't", "won't". The refusal to contract is the
  strongest machine-written tell.
- **Vary sentence length deliberately.** A three-word sentence next to a thirty-word one.
- **Numbers instead of adjectives, wherever a true number exists.** One compose file, one
  daily ping, one setting, four steps, one click.
- **The banned list, in full:** seamless, powerful, robust, enterprise-grade, best-in-class,
  cutting-edge, unlock, empower, leverage, revolutionise, game-changing, AI-powered,
  frictionless, turnkey, peace of mind, bank-grade, and every superlative implying
  exclusivity (only, first, unique, unmatched). Unverifiable claims cost the credibility of
  every checkable one.
- **Name the actor; never a bare "it"** where two subjects are in play.
- **Do not guess at the reader.** No "you probably", no assumptions about firm size or setup.
- **Concede facts, not ground.** The comparison already does this (Otter's bot-free mode and
  persistent speakers are stated plainly); keep the habit everywhere. Where a concession has
  a counterpoint, the counterpoint gets made.
- **Claims survive being checked, and comparisons carry their date.** Already built into
  comparison.ts; this is also where Vorkane's stale-Drive-claim lesson came from.
- **Plain labels beat clever ones.** "FAQ" beats "The awkward ones". A label may be evocative
  only if its meaning is obvious in half a second.
- **Parallel closers.** Paired elements end on matched short sentences.
- **A word budget for the skim layer: 400–600 words of prose** across headlines, ledes,
  labels. Devices and diagrams carry the rest.
- **Whole jobs, not first drafts** as the framing for every feature example: each ends with
  something genuinely delivered (the transcript attributed, the notes written, the task
  filed), not a capability list.

**Does not transfer, deliberately:**

- **First person singular.** Vorkane is one person selling himself; Nojoin is a product with
  the repo's own voice: plain, direct, second person, impersonal-imperative. "We/I" stays
  out of the marketing copy entirely.
- **Single committed theme.** Vorkane is light-with-a-toggle; Nojoin's dual
  `prefers-color-scheme` decision stands.
- **Question-form headings.** A Vorkane FAQ device; Nojoin's headings stay declarative,
  matching the product's register.
- **The zero-analytics posture.** Vorkane rejects even a beacon and reads visitors from edge
  metrics. Nojoin's settled decision (Cloudflare Web Analytics, cookieless, the only script)
  stands, but the edge-metrics-only alternative is recorded here as the fallback if the
  beacon ever feels wrong against the privacy positioning.

**Device adopted: the selective highlight.** One line per page, at most, carries a flat
`--action-tint` mark behind the text (the token already exists and is gate-audited). If
nothing on a page is punchy enough to deserve it, that is a finding about the page. The
budget is one: the highlight and the orange closer are separate devices, but a page never
gains a second highlight.

Two risks were examined and accepted rather than mitigated:

- Between P2 (gate repointed at the new tokens) and P9 (Jekyll deleted), the live Jekyll
  stylesheet is ungated. Nothing will edit it in that window; if something must, it is
  reviewed by eye.
- The Web Analytics token is only genuinely needed by cutover, not by P2 as the
  prerequisite list first claimed. P2 builds with the beacon slot empty if the token is
  not yet to hand.

## What the investigation changed

Three findings that were not in the original framing.

**The contrast gate already covers the marketing site.** `frontend/scripts/check-contrast.mjs`
points a `MARKETING` constant at `assets/css/style.scss` and audits it as a third theme
against a dedicated 30-pairing list. This is a repoint and rewrite of an existing gate, not
a new one. Related: `assets/css/**` is a trigger path on the CI `frontend` filter, and has
to move rather than sit alongside a new `site` filter. The script is dependency-free Node
(`node:fs`, `node:path` only), so the site CI job runs it without a frontend `npm ci`.

**`www.nojoin.co.uk` is already proxied through Cloudflare.** It resolves to Cloudflare
addresses with GitHub Pages behind them, and so does the apex, so the Redirect Rule at P8
has a proxied record to attach to. That makes a Worker **Route** available in place of a
Custom Domain, and the difference matters at cutover:

- A Custom Domain cannot be attached to a hostname that already has a CNAME record, so it
  forces the record to be deleted first. That is a gap in service, and rollback is a DNS
  change with propagation behind it.
- A Route sits in front of the already-proxied record. Adding it intercepts traffic
  atomically; removing it drops traffic back to the GitHub Pages origin immediately, with
  no DNS edit at all.

The plan therefore cuts over by adding a route, not by moving DNS. Because the route is
declared in `wrangler.jsonc`, "removing it" means reverting the cutover commit and letting
CI deploy — see the interrogation table above for why the dashboard shortcut is a trap.

**The redirect layer needs no Worker script.** `_redirects` is supported natively on Workers
static assets, including splat capture, with a limit of 100 dynamic rules. An assets-only
Worker omits `main` and `binding` entirely, so the deployment is a static bundle plus a
rules file and nothing executes per request.

## The URL contract

`frontend/src/app/setup/_components/LegalStep.tsx` and
`frontend/src/components/TelemetryNotice.tsx` both hardcode
`https://www.nojoin.co.uk/docs/TELEMETRY`. That URL is compiled into v2.3.0 and every
earlier image. Instances that are never upgraded will request it forever, so it can never
404.

`docs/` is flat — verified, no subdirectories — so single-segment rules cover every
historical URL. `site/public/_redirects`:

```
/docs/                 https://github.com/Valtora/Nojoin/blob/main/docs/README.md   301
/docs                  https://github.com/Valtora/Nojoin/blob/main/docs/README.md   301
/docs/:page.html       https://github.com/Valtora/Nojoin/blob/main/docs/:page.md    301
/docs/:page.md         https://github.com/Valtora/Nojoin/blob/main/docs/:page.md    301
/docs/:page            https://github.com/Valtora/Nojoin/blob/main/docs/:page.md    301
```

Rules are ordered most specific first, because the first match wins. Fragments need no
handling: a fragment is never sent to the server, and a browser reattaches it to the
redirect target itself, so `/docs/DEPLOYMENT#reverse-proxy-requirements` arrives at the
GitHub file with the anchor intact. GitHub renders `docs/*.md` with matching anchor slugs,
so the deep link survives.

Verification, by hand, on the workers.dev URL before cutover and on `www` after:

| URL | Expected |
| --- | --- |
| `/docs/TELEMETRY` | 301 to `.../blob/main/docs/TELEMETRY.md`, which returns 200 |
| `/docs/` | 301 to `.../blob/main/docs/README.md` |
| `/docs` | 301 to `.../blob/main/docs/README.md` |
| `/docs/DEPLOYMENT.html` | 301 to `.../blob/main/docs/DEPLOYMENT.md` |
| `/docs/DEPLOYMENT#reverse-proxy-requirements` | Lands on the GitHub file at that anchor |

## Architecture

```
site/
  package.json
  package-lock.json
  astro.config.mjs
  tsconfig.json
  wrangler.jsonc
  public/
    _redirects
    favicon.svg
    images/                    screenshots, light and dark pairs, plus the OG card
  src/
    styles/
      site.css                 @import tailwindcss, the shared tokens, and site-tokens
      site-tokens.css          site-only token families (see below)
    layouts/
      Base.astro               head, meta, analytics beacon, header, footer
    components/                Header, Footer, Hero, SectionBand, FeatureRow, CodeBlock, CompareTable
    data/
      github.ts                build-time star count and latest release
      comparison.ts            the comparison claims, each with a source URL and a checked date
    pages/
      index.astro
      compare.astro
      404.astro
```

### Tokens

`frontend/src/app/globals.css` is 888 lines, of which only lines 8 to 318 are tokens. The
rest is app furniture: a `react-datepicker` import, `@theme inline` utility mappings, a
settings-tab block, task-calendar overrides. Importing it wholesale into Astro would drag
all of that in.

The refactor:

1. Move the `:root`, `.dark` and `html[data-ui-density="compact"]` blocks into
   `frontend/src/app/tokens.css`.
2. `globals.css` gains `@import "./tokens.css";` at the top and loses those blocks. Nothing
   else changes, and the app's compiled CSS should be byte-identical in effect.
3. `site/src/styles/site.css` imports the same file by relative path.

Verification that the move was inert: `npm run build` in `frontend/` succeeds, and
`npm run check:contrast` reports the same ratios as before the move.

Two token families are site-only and stay out of the shared file, in
`site/src/styles/site-tokens.css`:

- `--code-*` for syntax highlighting in the quick-start block. The app has no code blocks.
  These exist today in `assets/css/style.scss` but only in dark values, because the current
  site is dark-only. Light values are new work.
- Any band tokens the marketing composition needs that have no app equivalent.

The site uses `prefers-color-scheme` while the app uses a `.dark` class, so
`site.css` maps the class-based blocks onto a media query:

```css
:root { /* light values from tokens.css */ }
@media (prefers-color-scheme: dark) { :root { /* the .dark block */ } }
```

The mapping is mechanical and derived from the same source, so the values cannot diverge.

### The contrast gate

`check-contrast.mjs` changes from three themes to four:

| Theme | Tokens | Pairings |
| --- | --- | --- |
| `light` | `tokens.css` `:root` | `PAIRINGS`, unchanged |
| `dark` | `tokens.css` `.dark` over `:root` | `PAIRINGS`, unchanged |
| `site-light` | `tokens.css` `:root` plus `site-tokens.css` light | `SITE_PAIRINGS`, new |
| `site-dark` | `tokens.css` `.dark` plus `site-tokens.css` dark | `SITE_PAIRINGS`, new |

`SITE_PAIRINGS` replaces `MARKETING_PAIRINGS`. It keeps the pairings that still apply
(headings, body, links, hairlines, focus ring, footer, code tokens) and adds the ones the
new furniture introduces: text on each section band, the hero CTA pair, the comparison
table's header row and its zebra striping, and the badge that carries the star count.
Pairings for furniture that no longer exists, chiefly the quick-start step counter, go.

The script currently hardcodes `floats: false` for marketing on the grounds that the site
has no floating elements. That stays true: the new site has no modals either.

### Build-time data

`site/src/data/github.ts` fetches the star count and the latest release tag from the GitHub
API at build time and bakes both into the HTML. In CI the request carries the workflow's
`GITHUB_TOKEN` so it is not subject to the 60-per-hour unauthenticated limit.

The build must not fail on a rate-limited or unavailable API. Fallbacks:

- Latest release falls back to reading `docs/VERSION` from the checkout.
- Star count falls back to omitting the number and rendering the button as "Star on GitHub"
  with no count, which is a legitimate state rather than a placeholder.

The same resilience rule shapes the comparison source-URL check: it runs in the weekly
workflow as its own job, red on failure, and the deploy job does not depend on it.

### Analytics

Cloudflare Web Analytics beacon in `Base.astro`, loaded `defer`. It is the only script on
either page. The site token is not a secret and can be committed, which keeps the page
buildable by anyone cloning the repo.

## Content

Every claim on the site is traceable to `README.md`, `docs/USAGE.md`, `docs/CAPTURE.md`,
`docs/ARCHITECTURE.md` or `docs/TELEMETRY.md`. Nothing is invented for marketing effect.

### Landing page

1. **Hero.** Headline, one-sentence subhead, two equal CTAs, dashboard screenshot below.
   "Get Started" scrolls to the quick-start section rather than leaving the page.
2. **Positioning strip.** The three things that distinguish Nojoin: no bot joins the call,
   works with any meeting platform without an integration, runs on your own hardware.
3. **Four feature sections**, alternating page and card bands, each with one screenshot:
   Meeting Edge, transcript with speaker attribution, documents, calendar.
4. **Quick start.** The compose command in a syntax-highlighted block, four steps, linking
   to `docs/GETTING_STARTED.md` for the full path.
5. **Privacy band.** Self-hosted, AGPLv3, optional fully local inference with Ollama,
   telemetry off in one setting. Links to `docs/TELEMETRY.md`.
6. **Comparison teaser** linking to `/compare`.
7. **Footer.** Documentation links, repository, licence, support, current version.

### Comparison page

Three competitors: Otter, Granola, Fireflies.

**Research obligation.** Every competitor claim is verified against that vendor's own
current documentation or product pages, not from prior knowledge. Each claim in
`comparison.ts` carries a source URL and the date it was checked, and the page renders those
as footnotes. A claim that cannot be sourced does not go on the page.

**No competitor pricing.** Pricing changes without notice and a stale price is the error
people screenshot.

**Framing.** Lead with what Nojoin does and what each competitor does, on shared axes, and
let the gaps speak. A page organised around competitors' weaknesses reads as an attack piece
and costs credibility with a technical audience.

Proposed axes, subject to what the research supports:

- How audio is captured: a bot joins the call, or the browser captures the tab
- Speaker attribution, and whether it persists across meetings
- Where processing happens
- Where recordings and transcripts are stored
- Self-hosting
- Source availability and licence
- Model choice, including bring-your-own-key and fully local inference
- Live in-meeting guidance
- Calendar integration
- Search across meetings, notes and documents
- Export and data portability

## Phases

Four pull requests, in order. Each phase is a commit or a small run of commits and has an
acceptance test that is actually run. The original single-branch shape was abandoned during
interrogation: `workflow_dispatch` cannot fire until the workflow is on the default branch,
cutover needs a deploy from `main`, and the post-cutover settling period would have held a
branch open for weeks.

**PR 1** carries P1 and this plan document. **PR 2** carries P2 through P7. **PR 3** is the
one-line cutover (P8). **PR 4**, after the settling period, is the teardown (P9) and
deletes this document.

### P1: scaffold and deploy pipeline — PR 1

Prove a hello-world Worker reaches a `workers.dev` URL before any content exists, because a
deploy pipeline that is debugged after the site is written is debugged against two unknowns
at once. This PR merges to `main` first precisely so the real triggers can fire; the
placeholder on workers.dev is harmless because no route exists yet.

- `site/` scaffold: Astro, Tailwind v4, TypeScript, a placeholder `index.astro`
- `site/wrangler.jsonc`: assets-only, no `main`, `not_found_handling: "404-page"`,
  `workers_dev: true`, account id `54b2d24ea54cb676eea55814237b88c9`, **no route yet**
- `.github/workflows/site-deploy.yml`: builds and deploys on push to `main` under `site/**`,
  on `release: published`, on a weekly schedule, and on `workflow_dispatch`. The weekly run
  also executes the comparison source-URL check as a separate, non-blocking job
- `.gitignore`: `site/dist/`. Note the existing `/dist` entry is root-anchored and does not
  cover it
- CI: a `site` filter and a job running the site build plus the contrast gate. `assets/css/**`
  moves off the `frontend` filter in the same change
- `site/package-lock.json` is generated and validated under nvm-managed Node 26 / npm 11,
  never the distro Node 20 / npm 9 that non-interactive shells fall back to

**Acceptance:** the placeholder page loads over HTTPS on the workers.dev URL, deployed by CI
rather than by hand, from a `main` merge.

### P2: design foundation — PR 2

- Extract `frontend/src/app/tokens.css`, with `globals.css` importing it
- `site/src/styles/site.css` and `site-tokens.css`, including new light values for `--code-*`
- Repoint and rewrite the contrast gate for four themes
- `Base.astro`, `Header.astro`, `Footer.astro`, and the shared primitives

**Acceptance:** `npm run check:contrast` passes across four themes; `npm run build` in
`frontend/` succeeds; the app renders unchanged.

### P3: redirect layer — PR 2

- `site/public/_redirects` as specified above
- Deploy and verify every row of the URL contract table against the workers.dev URL

**Acceptance:** all five verification URLs behave as specified, checked with `curl -I`.

### P4: demo instance and seeding — PR 2

- A private demonstration stack on the development host: local only, no reverse proxy, no
  public DNS, separate ports and volumes. The compose file stays on the host and is not
  committed
- Inference comes from an Ollama server on the LAN running `qwen3:14b`, with the context
  window lowered to 16384. The shipped default is 131072, which is right for a hosted
  provider and wrong for a 14B model on a 16GB card: the KV cache for a 128K window will
  not fit alongside the weights. `LLM_PROVIDER`, `OLLAMA_API_URL` and
  `OLLAMA_CONTEXT_WINDOW` are environment overrides that take precedence over
  `config.json`, so they belong in the stack's `.env`; the model name has no override and
  is chosen in the first-run wizard
- `scripts/seed_demo_instance.py`: DB-level fixtures giving the recordings list, calendar and
  tasks enough density to look like a real install. It imports `backend.models` and uses
  the application's own session, so the models and Alembic stay the single source of truth
  and no column list is hand-written. It aborts if its own marker is already present, and
  `--reset` deletes only the rows it created
- Eight seeded recordings over three weeks describe a mixed small business, with fictional
  people, tags and tasks around them. Calendar events are seeded independently so the
  current week is populated regardless of when the recordings fall
- One real recording comes from public-domain, meeting-shaped audio — a NASA media day
  briefing — imported by hand and processed normally, so waveforms, diarisation and
  timings are genuine. It is labelled honestly: real title, real speaker names, provenance
  noted. No invented names sit over real people's words, and no seeded recording is given
  an invented transcript
- The demonstration stack shares the host's single RTX 2080 SUPER with the live instance,
  so seeding does no GPU work and the import is run on its own

**Acceptance:** the demo instance boots, the seed script populates it, and it looks like an
established install rather than a fresh one.

### P5: landing page — PR 2

Implements the locked design direction above, at Variant C's scale and rhythm. Built
against placeholder frames at the final aspect ratios, so nothing blocks on the
screenshots.

**Acceptance:** renders correctly at 360px and 1920px in both themes, with no horizontal
scroll at any width; contrast gate passes.

### P6: comparison page — PR 2

Research first, then build. Sources and dates recorded per claim. The weekly source-URL
check from P1 starts to matter here: it catches a footnote link dying, while the dated
footnotes remain the disclosure for facts that change behind a live URL.

**Acceptance:** every competitor claim on the page has a source URL and a checked date, and
each source has been opened and read rather than inferred.

### P7: screenshots — PR 2

Eleven images: dashboard hero plus four feature shots, each in light and dark, and one
dedicated 1200×630 Open Graph card composed from the mark, wordmark, headline and
descriptor. The screenshots swap by `<picture>` on `prefers-color-scheme`; the OG card is a
single fixed image because OG cannot theme-swap. All compressed, sized, and checked for
anything identifying.

Captured with Playwright driving headless Chromium against the demonstration stack:
viewport 1920×1080 at device scale factor 2, both colour schemes in one run, with the app's
navigation collapsed, so the ten shots stay consistent with each other and can be retaken
after any content change. The capture script lives outside the repository, which has no
browser-automation dependency and should not gain one for a job that runs about once a year.
Output is WebP at quality 82, downscaled to 2560×1440 and served at the 1920×1080 layout
size.

Panel state is set by writing the persisted `navigation-storage` store rather than by
clicking chevrons, which is both faster and immune to a control moving with the viewport.
The same mechanism marks the driver.js onboarding tour as seen: it fires after a `getUserMe`
call resolves, so it can appear later than `networkidle` and outrace any click-to-dismiss.

Two components change alongside the files. `Shot.astro` hardcodes `.svg` in both `srcset`
and `src` and declares a 1440×900 layout size, so it is repointed at `.webp` at 1920×1080.
`Base.astro` has no `og:image` and no Twitter card tags at all, so it gains both.

The Meeting Edge shot is the awkward one: the panel lives inside `RecordingStatusDisplay`,
which the recording detail page renders only for an in-flight recording, so a finished
recording never shows it. It renders purely from `transcript.meeting_edge_payload`, so the
shot is taken by generating a real payload from the Artemis transcript and then staging that
recording back into an in-flight status.

**Acceptance:** no real personal data in any image; the page's layout is unchanged from the
placeholder version beyond the images themselves.

### P8: cutover — PR 3

1. Confirm the site is good on workers.dev, including the URL contract
2. Open and merge a one-line PR adding the route `www.nojoin.co.uk/*` to `wrangler.jsonc`;
   CI deploys it
3. Verify `www.nojoin.co.uk` serves the new site and the five redirect URLs still behave
4. Add the apex 301 to `www` as a Cloudflare Redirect Rule (the apex is already proxied,
   verified, so the rule has a record to fire on)
5. Leave GitHub Pages enabled and the origin untouched for a settling period

**Rollback:** revert the cutover commit and let CI deploy. Traffic returns to the GitHub
Pages origin immediately. Deleting the route in the dashboard is explicitly not the
rollback path: the weekly scheduled deploy from `main` would re-add it.

### P9: teardown — PR 4

Only once the new site has been live and correct for a settling period.

- Disable GitHub Pages in repository settings
- Delete `_config.yml`, `_layouts/`, `_includes/`, `assets/css/style.scss`, `docs/index.md`,
  `CNAME`
- Keep `assets/images/nojoin-mark.svg`
- Check `README.md` still renders correctly on GitHub. It has been carrying markup shaped by
  the Jekyll stylesheet, and although it is ordinary Markdown it has not been read without
  that stylesheet in a while
- Remove the now-dead Liquid tolerance in `scripts/validate_docs.py`
  (`should_skip_target` skips `{{` and `{%`)
- Rewrite the "The marketing site" section of `docs/DESIGN.md`
- Add site commands to `CONTRIBUTING.md` and `docs/DEVELOPMENT.md`, including the
  nvm-sourced-shell requirement for `site/` work
- Delete this plan document

## Prerequisites the user must provide

Blocking, listed in the order they are needed.

1. **`CLOUDFLARE_API_TOKEN` repository secret.** Scopes: Account, Workers Scripts, Edit; and
   Zone, Workers Routes, Edit, on the `nojoin.co.uk` zone. The account id is already in the
   repo and is not a secret. Needed before P1 can be proved.
2. **Cloudflare Web Analytics site token** for `www.nojoin.co.uk`. Needed by P8; P2 builds
   with the beacon slot empty until it exists.
3. **Selection and manual import of the public-domain meeting audio.** Needed before P7.
4. **Apex redirect rule** at P8, unless it already exists.
5. **Disable GitHub Pages** at P9.

The `cloudflare-api` MCP server is unauthenticated in this session, so account state cannot
be read from here. Anything requiring it is done by hand in the dashboard.

## Risks

| Risk | Mitigation |
| --- | --- |
| Token extraction regresses the app's styling five commits after the restyle shipped | It is a pure CSS move with no value changes. Gated by the frontend build and by the contrast gate reporting identical ratios |
| `site/package-lock.json` generated by the distro npm 9 differs from what CI's npm produces | All `site/` npm work runs under nvm-managed Node 26 / npm 11, matching CI. `npm ci` remains the validation, but the generating toolchain is now the same one CI uses |
| Build-time GitHub API call fails and breaks a scheduled deploy | Both values have fallbacks; neither failure aborts the build |
| A comparison source URL dies and nobody notices | The weekly non-blocking URL check turns the run red without blocking the deploy |
| A `_redirects` rule is wrong and breaks the URL contract after cutover | Every rule is verified on workers.dev before the route exists, and rollback is reverting the cutover commit |
| Screenshots leak real meeting content | Public-domain source audio and a dedicated demo instance, never the live one. Every image reviewed before commit |
| Comparison claim is wrong or goes stale | Sourced and dated per claim, no pricing, and the axes chosen so the answers are structural rather than roadmap-sensitive |
| The Jekyll stylesheet is ungated between P2 and P9 | Accepted: nothing edits it in that window, and anything that must is reviewed by eye |

## Out of scope

- Any docs hosting on the site
- Changes to the app, other than the `tokens.css` extraction
- Changes to `.github/workflows/release.yml` beyond the site rebuild trigger
- SEO work beyond correct titles, descriptions, Open Graph tags, a sitemap and `robots.txt`

## Definition of done

- [ ] `www.nojoin.co.uk` serves the Astro site from Cloudflare Workers, apex 301s to it
- [ ] All five URL contract rows verified by hand, before and after cutover
- [ ] Every page renders correctly in light and dark, on a phone and a desktop, with no
      horizontal scroll
- [ ] Contrast gate passes over the site's tokens in both themes, in CI
- [ ] Jekyll removed, GitHub Pages disabled, `README.md` still renders correctly on GitHub
- [ ] Site build runs in CI on changes under `site/`
- [ ] Scheduled rebuild and release-triggered rebuild both observed working, including the
      non-blocking source-URL check
- [ ] `docs/DESIGN.md`, `CONTRIBUTING.md` and `docs/DEVELOPMENT.md` updated
- [ ] This plan document deleted in the final commit

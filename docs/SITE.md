# The Marketing Site

The brand, voice and maintenance rules for `www.nojoin.co.uk`, which lives in `site/` as an
Astro build deployed to Cloudflare Workers static assets. This document is the durable
record: the site's working plan documents are PR-scoped and deleted when their work ships,
but the decisions below outlive them. [DESIGN.md](DESIGN.md) governs the app; the site runs
the app's design system at marketing scale, and where the two overlap, DESIGN.md is the
authority on tokens and this document is the authority on the site's composition and copy.

## What the site is

Three pages: landing, comparison and the managed service. Documentation stays on GitHub as
`docs/*.md` and the site links out to it; the site never holds a copy that can drift.
`/docs/*` URLs 301 to the corresponding GitHub blob URL forever, because released images
hardcode `www.nojoin.co.uk/docs/TELEMETRY` and a URL compiled into an image can never 404.

The rebuild shipped two pages and recorded "there is no hosted or paid offering" as the
reason, while deliberately building the nav and layout to take a third page later. That
turned out to be worth doing: `/managed/` arrived afterwards and cost one nav entry, one
landing band and no redesign.

Both themes follow `prefers-color-scheme` by default, and a toggle in the header lets a
visitor override that. The choice is stored and applied before the first paint, so there is
no flash on the way to a dark page, and the screenshots follow the active theme rather than
the system one.

This replaces an earlier decision to ship no JavaScript of our own. That position bought
one thing — a page with nothing to break — and it cost visitors the ability to look at the
product in the theme they wanted. The toggle wins because the screenshots are most of the
page's argument and half of them were unreachable to anyone whose system setting disagreed.
What remains true is that there is no framework and no hydration: two small inline scripts,
one to stamp the stored choice on the root element and one to wire the button, alongside
the Cloudflare beacon.

Fonts are self-hosted: a page whose pitch is that nothing leaves your server does not fetch
fonts from a CDN.

## Design direction

Locked by decision, rendered and chosen from variants, and not to be re-litigated casually:

- **Centred hero stack**: headline, subhead, two equal CTAs (Get started, Star on GitHub),
  full-width screenshot below.
- **Display scale is "Statement"**: 64px hero via `clamp(2.75rem, 6.5vw, 4rem)`, section
  headings 2.25rem, generous vertical rhythm to match. Geist stays semibold (600) with
  -0.025em display tracking; no new weight enters the system.
- **Every screenshot sits in a minimal browser-chrome frame**: three dots, a muted
  `nojoin.your-server.net` URL pill, hairline border, 12px radius, the resting shadow in
  light only. The frame signals "a web app you self-host", which is half the pitch.
- **Feature rows stack**: copy at reading width, screenshot beneath it in a container wider
  than the rest of the page, on bands that alternate the page and card surfaces. The
  alternating rhythm survives as the copy block changing sides down the page; the text
  itself stays left-aligned. This replaces a side-by-side layout, which gave the screenshot
  half of a 72rem wrap and so rendered a 1920px capture at about 28% — unreadable, which
  defeats the point of showing the interface at all.
- **Colour is neutral plus one orange closer**: the loud surface is the full-bleed
  orange-700 band at the foot. No cream tints and no glass.
- **Three marketing effects, and no more.** The app's flat canon governs the product,
  where a lifted control is noise in a workspace someone stares at all day. The site is a
  page whose job is to be looked at once, so it takes three departures, all site-only and
  all declared in `site-tokens.css` rather than in the shared app tokens:
  a halo and a 1px inset white top edge under the primary button (`--glow-action`,
  `--glow-inset`); one soft radial wash off the top right behind the hero and the agent
  band (`--hero-wash`); and an orange edge on every screenshot frame (`--frame-accent`),
  because the captures are light interface on a light page and a divider-coloured hairline
  did nothing at reading distance. All three are decoration over fills that already answer
  to the contrast gate, so none of them can hide a failing pair. The wash is the only
  gradient on the site, it carries no information, and it degrades to a plain surface.
  These match the sibling brand Vorkane's treatment deliberately — the same person sells
  both — but they use Nojoin's own orange ramp rather than Vorkane's, so the site and the
  app never disagree about what the brand colour is. The glow derives from orange-600
  rather than the button's own orange-700, because a halo the same darkness as its fill
  reads as a smudge.
- **The header is sticky**, with a solid fill rather than a blurred scrim. Glass is out by
  the flat canon, and `backdrop-filter` over text this dense costs a repaint per scroll
  frame for an effect the design does not want.
- **The agent band sits directly under the strip**, on the page surface with the hero's
  wash and hairlines closing it, so the alternating band rhythm below is untouched. It
  carries two infographics rather than prose: a flow card tagging each step of one real
  post-meeting job with the actor who performs it, and a six-cell map of the thirty tools
  grouped by what they touch. Both exist because there is no honest screenshot to take
  (see below), and because a capability list is more convincing shown than described.
- **The headline is no-bot led**: "Meeting notes, with no bot in the call". Self-hosting
  lands in the subhead.
- **The agents section carries no screenshot, by decision.** Every other feature section on
  the landing page is led by its shot. That capability happens in Claude's or Codex's
  window, not Nojoin's, and the frame chrome reads `nojoin.your-server.net` — so a framed
  capture of someone else's interface would be a false image on a page that argues from
  checkable ones. Nojoin's own screens cannot carry the claim either: the app records that
  an utterance was manually edited but never distinguishes an assistant's edit from a web
  one, so a task list an assistant filed is indistinguishable from a typed one. The flow
  card and the tool map take the visual weight instead. Should the app ever surface MCP
  provenance, this band earns a real shot and should get one.
- **The selective highlight**: at most one line per page carries a flat `--action-tint` mark
  behind the text. If nothing on a page earns it, that is a finding about the page, not a
  reason to lower the bar. The highlight and the closer are separate devices; a page never
  gains a second highlight.

Tokens are shared with the app: `site/src/styles/site.css` imports
`frontend/src/app/tokens.css` by relative path, a build-time transform maps the app's
class-based dark theme onto the site's media query, and `site/src/styles/site-tokens.css`
holds the families the app has no equivalent of (syntax highlighting, frame chrome, the
closer-band buttons, the marketing scale). The contrast gate audits the site as two extra
themes; see DESIGN.md's accessibility section.

## Voice

The site speaks the product's own register: plain, direct, a little opinionated, second
person, sentence case, British English, no emoji. It shares a writing standard with the
sibling brand Vorkane, adapted rather than copied — Vorkane sells a person and writes in the
first person singular; Nojoin is a product and never says "I" or "we" in marketing copy.

**One page is exempt: `/managed/`.** What it sells is not the product but one named person's
time, and a service provided personally cannot honestly describe itself as "it". That page
writes in the first person singular, names Tay outright, and says so in its own first
paragraph. The full legal name stays off the site and out of the repository by decision.
The exception is scoped to that page and does not travel: the landing
band that points at it stays in the product's voice and refers to "Nojoin's author" in the
third person, so a visitor meets one narrator per page rather than two per scroll. "We"
stays banned everywhere, including there — one person is an "I", never a "we", and the
corporate plural is exactly the register the rest of these rules exist to avoid.

The rules:

- **Contract everything.** "It's", "can't", "won't", "you've". The refusal to contract is
  the single strongest tell that a machine wrote it.
- **Vary sentence length deliberately.** A three-word sentence next to a thirty-word one.
- **Numbers instead of adjectives**, wherever a true number exists. One compose file, one
  daily ping, one setting, four steps, one click. A technical reader takes numbers as
  evidence and adjectives as sales.
- **Name the actor.** Never a bare "it" where two subjects are in play.
- **Do not guess at the reader.** No "you probably", no assumptions about their setup or
  their size. Make the claim about the thing, not about them.
- **Concede facts, not ground.** Where a competitor does something well, say so plainly;
  where a concession has a counterpoint, make it. Candour about checkable limits is what
  buys the credibility of everything else.
- **Plain labels beat clever ones.** A label may be evocative only if its meaning is still
  obvious in half a second.
- **Parallel closers.** Paired elements end on matched short sentences.
- **Whole jobs, not first drafts.** Every feature example ends with something genuinely
  delivered: the transcript attributed, the notes written, the task filed.
- **A word budget**: 400–600 words of prose in the skim layer (headlines, ledes, labels).
  Devices, screenshots and the table carry the rest.

**Banned everywhere**: seamless, powerful, robust, enterprise-grade, best-in-class,
cutting-edge, unlock, empower, leverage, revolutionise, game-changing, AI-powered,
frictionless, turnkey, peace of mind, bank-grade — and every superlative implying
exclusivity (only, first, unique, unmatched), because an unverifiable claim costs the
credibility of every checkable one.

## Claims and the comparison page

Every claim on the site is traceable to `README.md` or a file in `docs/`; nothing is
invented for marketing effect.

The comparison page holds itself to the standard it would want applied to it:

- Every competitor claim is verified against **that vendor's own current documentation**,
  carries the exact URL read and the date it was checked, and renders as a footnote.
- A claim that cannot be sourced does not go on the page. It appears as an explicit "Not
  stated in current docs" cell rather than a guess.
- **No competitor pricing, ever.** Prices change without notice, and a stale price is the
  error people screenshot.
- Where a competitor is good, the cell says so. The concessions are what make the
  structural gaps (self-hosting, licence, model choice) believable.
- The weekly deploy workflow runs a non-blocking source-URL check: a dead footnote link
  turns the run red without giving a competitor's web server a veto over the site's own
  rebuild. Changed facts behind live URLs still need a human re-read; treat any vendor
  feature announcement as a trigger to re-check its row.

## The managed service

`/managed/` sells one thing: Tay scoping, installing and maintaining a private
instance for an organisation. The commercial shape below was settled deliberately and the
reasoning matters more than the numbers, because the numbers will move.

- **The customer owns the hardware and pays for it directly.** The fee is labour and nothing
  else. That removes idle spend, cost overruns and supplier risk from the offering in one
  move, and it keeps "your server, your data" literally true — which the rest of the site
  spends two pages arguing for.
- **The page describes the machine, not its price.** 8 GB of VRAM, quoted from
  DEPLOYMENT.md, with CPU-only named as slower rather than absent. The same reasoning that
  bans competitor pricing applies to a supplier's: a quoted cloud price goes stale and a
  stale price is the error people screenshot. It is also honest that a customer with a
  suitable machine already pays nothing extra.
- **£250 a month, ten to twenty-five people.** The floor sits at ten because fixed hardware
  cost spread across five people makes the smallest customer the worst value in the book,
  and no wording fixes an intercept. Above twenty-five is a conversation rather than a
  published tier: there is no customer of that size yet, and an invented price is exactly
  the unbacked number the comparison page's standard exists to prevent.
- **Setup is £950, waived on a twelve-month commitment.** Month one is the heaviest month.
- **Nothing on the page is a promise that breaks on a bad week.** No uptime figure, no SLA,
  no capacity claim. Same day if the instance is down, next working day otherwise, UK hours,
  and the limit named rather than implied. Monitoring is listed as part of the fee because
  an outage promise is only meetable if something outside the network notices the outage.
- **Upgrades run monthly, deliberately slower than releases.** Ten releases shipped in the
  seven weeks before the page was written. Applying each one to a customer's production
  system means changing it twice a week, and this repository has already met an Alembic
  back-stamp that crash-loops a newer image.
- **The AGPL position is stated as a feature, not conceded as a limit.** Prioritised
  requests get read first and get an answer; whatever gets built ships to everyone under the
  same licence. The licence forbids a private version, so nobody is locked in, and the thing
  being sold is time rather than access. That is a strong sentence and a weak omission.

Two things that are true and stay off the page by decision: the processing agreement, and
the admin access that makes maintenance possible. No sentence there claims the data is
unreachable, so nothing needs retracting on a call.

**The call to action is an address, not a booking page.** A self-hosted scheduler was the
plan, on the reasoning that a page arguing for self-hosting should book its own calls
through software it hosts. Cal.com then moved its production codebase into a private
repository: the public repo became `calcom/cal.diy`, relicensed from AGPL-3.0 to MIT,
stripped of teams and workflows, and documented by its own maintainers as being for
personal rather than production use. No Docker image is published for it and its app URL is
baked in at build time, so running it would mean building and rebuilding a Next.js monorepo
to keep one booking type alive — under the page's only call to action, with nothing behind
it if the build broke. `hello@nojoin.co.uk` cannot go down. It is a Proton alias on the
custom domain rather than the inbox itself, because an address in public markup gets scraped
and an alias can be replaced without moving the mailbox.

## Maintenance

- The site deploys from `main` only: a push touching `site/`, a published release, a weekly
  schedule, and manual dispatch. Rolling back the production route is a git revert of the
  cutover commit, never a dashboard action, because the next scheduled deploy re-applies
  whatever `wrangler.jsonc` says.
- Build-time data (star count, latest release) degrades gracefully: the release falls back
  to `docs/VERSION`, the star count to no number. No API outage may fail a build.
- Screenshots are real captures from a seeded demonstration instance processed through the
  genuine pipeline, in light and dark pairs swapped by `<picture>`. The source audio is
  public-domain material, labelled honestly with real titles and speakers; no invented
  names sit over real people's words.

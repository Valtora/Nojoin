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
  `nojoin.your-server.net` URL pill, an orange edge, 14px radius and `--lift-strong`. The
  frame signals "a web app you self-host", which is half the pitch.
- **Feature rows put the copy beside the shot**, alternating sides down the page. This
  reverses the stacked layout, which itself replaced a side-by-side one, so the reasoning
  matters more than the arrangement. Side-by-side failed the first time because it gave a
  1920px full-app capture half of a 72rem wrap and rendered the interface at about 28% —
  unreadable, which defeats the point of showing it. Stacking fixed that by giving the shot
  the full width. **What changed is the captures, not the layout.** Each shot is now cropped
  to the thing its row is about — 540 to 810 logical pixels rather than 1600 — so half a wrap
  shows it at roughly life size. The stack had started to look like four identical pictures
  of the same window, each too big for what it was saying. If a future shot goes back to
  being a whole window, this layout breaks again, and **the fix is the crop, not the grid.**
- **A feature row earns its place with its screenshot.** The Calendar row was cut: its
  capture was the dashboard shot scrolled down a little, so the row spent a full section of
  vertical rhythm showing something the page had already shown. Calendar sync is not
  demoted — it still leads a tool group in the agent band's showcase and holds a sourced row
  on the comparison page. What it lost was a screenshot that carried no new information, and
  a row whose shot duplicates another row's is a finding about the row rather than a reason
  to keep scrolling. `calendar-light.webp` and `calendar-dark.webp` went with it, because an
  unreferenced capture in `public/` is a thing the next person re-adds by accident.
- **No hard bands.** Sections used to alternate a full-bleed card surface against the page
  with hairlines top and bottom. That read as a stack of documents: every boundary was an
  edge. The page is now one continuous surface and depth comes from the content floating on
  it — framed screenshots, the flow card, the tool showcase, the price card — each carrying
  `--lift`. `.band` keeps only `--band-wash`, a gradient faint enough that two adjacent
  bands never look like a seam. In dark it lightens rather than casting, because a black
  shadow on a near-black page is invisible. **`.band` and `.section` must still strictly
  alternate**, and it is worth checking after any section is added or removed: the wash is
  faint, so two `.band`s in a row do not look like a seam, they look like nothing at all, and
  the rhythm quietly disappears. Removing the privacy section left three consecutive bands
  before anyone noticed.
- **The spell check is in the notes screenshot on purpose.** The generated demo notes use
  American spellings and the editor underlines them, which read as errors in Nojoin's output
  until the copy beside the shot claimed them: the row now points at the red line under
  "emphasizing" and says the spell check reads British English. That turns an artefact into
  the feature it actually is. It is also the honest reading — nothing was retouched, and four
  attempts to suppress the underlines failed because the editor re-renders through DOM and
  stylesheet overrides alike.
- **The quick-start code block is dark in both themes**, and it is the only surface that
  ignores the theme toggle. It followed the theme until a reader pointed out it was still
  hard to look at in light: the syntax colours were legible on the measurements, but the
  block sat at 1.04:1 against the page, so there was nothing to look *at*. Code being dark on
  a light page is a convention rather than a departure, and it is the only version of this
  that also works in dark, where a darker fill has nowhere to go. See DESIGN.md for the
  numbers.
- **Colour is neutral plus one orange closer**: the loud surface is the full-bleed
  orange-700 band at the foot. No cream tints and no glass.
- **The closer is the managed offer, and there is no repeat-CTA band.** The foot of the page
  used to carry "Your meetings, on your own server, tonight" with Get started and Star on
  GitHub — the same two calls the hero already makes, one screen further down a page that had
  grown too long. It went. The managed teaser took the orange treatment in its place, so the
  page still ends on a deliberate surface rather than trailing off, and the last thing a
  visitor reads is the one offer the hero does not make. A page that has to ask twice is
  usually too long, which is what the rest of this pass was about.
- **The landing page carries no privacy section.** It had one, and it was cut when the page
  was shortened: those claims are made better elsewhere. Self-hosting and local inference
  lead the three-up strip, the licence is in the footer, telemetry has a footer link on every
  page, and `/compare/` argues the whole privacy case in sourced rows rather than assertions.
  A section repeating them on the apex page was the fourth time a visitor met the same
  argument.
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
- **The nav carries the two destinations a visitor looks for by name**: Home, Quick start,
  Pricing, Compare, Docs. "Quick start" jumps to the landing page's install section, which
  had no way to reach it from another page; "Pricing" is what the third page is called
  everywhere else on the web, and it replaced "Managed" rather than joining it, because two
  entries pointing at one page is a worse header than a slightly generic label. The link is
  `/#quick-start` rather than `#quick-start`: the section exists on one page only.
  There is no menu button and no second script. The nav already wraps — measured, not
  assumed, it holds 5 links plus the star badge and the toggle inside the content box at
  320px, with the header growing a row instead of the page scrolling sideways. A hamburger
  is worth revisiting only if the nav stops fitting.
- **Every in-page anchor reserves the sticky header's height.** `section[id]` carries
  `scroll-margin-top`, 8.5rem while the nav is wrapped and 5rem once it fits one line. The
  browser scrolls a hash target to y=0, which is exactly where a sticky header already is, so
  without this the quick-start heading lands underneath it. The mobile value is the larger of
  the two, which is the opposite of the usual direction and the reason it is written out.
- **The header is sticky**, with a solid fill rather than a blurred scrim. Glass is out by
  the flat canon, and `backdrop-filter` over text this dense costs a repaint per scroll
  frame for an effect the design does not want.
- **The agent band sits directly under the strip**, on the page surface with the hero's
  wash. It carries three devices rather than prose. A flow card walks one real post-meeting
  job and tags each step with the actor who performs it, chevrons between the steps so it
  reads downward as a sequence rather than as a table. A six-card showcase groups the thirty
  tools by what they touch, each led by a glyph. A wider card carries the CRM bridge on its
  own, because an assistant reconciling your People library with anything it can already
  reach is the claim with no equivalent in the comparison, and it was a clause in a
  paragraph. The actor pills say "Agent", never a vendor name: more than one assistant is
  supported and the claim is about assistants generally. The prose names Claude and ChatGPT,
  which is what a reader installs; OpenAI renamed the Codex desktop app to ChatGPT on 9 July
  2026, and Codex survives as a mode inside it. `docs/MCP.md` still says Codex throughout,
  deliberately: its instructions carry literal commands and paths (`codex mcp logout`,
  `~/.codex/config.toml`, the "Codex MCP Credentials" keychain entry) that the rename did not
  change, and its troubleshooting section records behaviour observed on a specific Codex
  build that nobody has re-tested since.
- **The headline concedes the commodity and claims the hard part**: "Transcription is
  easy. Agentic meeting intelligence isn't." This reverses the earlier no-bot headline
  deliberately, and then reverses its first agent-led replacement too. Not joining the call
  is a strong feature that three competitors match some version of, and transcription is a
  solved problem that nobody wins on. What is not solved is being the bridge between
  whatever agent someone already uses and the rest of their stack, which is what Nojoin was
  built to be and what none of the three documents. The headline says so by conceding the
  easy half out loud, which is more convincing than claiming the hard half on its own.
  "Agentic" is jargon and stays anyway: on the landing page it flatters a reader who knows
  the word, and the subhead immediately spells it out in plain terms for one who does not.
  The `/managed/` page, whose reader is far less likely to be technical, avoids it entirely.
  No-bot did not disappear; it opens the subhead and leads the strip beneath the hero.
- **The agents section carries no screenshot, and this has now been tried both ways.** The
  original reason still stands: the capability happens in Claude's or ChatGPT's window, not
  Nojoin's, and the frame chrome reads `nojoin.your-server.net`, so a framed capture of
  someone else's interface would be a false image on a page that argues from checkable ones.
  A shot of Nojoin's *own* transcript showing the `AI corrected text` and `AI corrected
  speaker` labels is honest, so it was added — and then removed, because in place it said the
  same thing as the transcripts row two sections below it and sat under the bridge card with
  no copy beside it. Honest is not the same as earning its place. The three devices carry the
  band instead: the flow card, the tool showcase and the bridge card, each with a claim no
  capture makes — the actors in a real post-meeting job, thirty tools, the CRM
  reconciliation. **The rest of the surface still has none.** A task list an assistant filed
  is indistinguishable from a typed one, so the band does not claim it in an image.
- **Screenshots are cropped to the thing the row is about, and sized for half a wrap.** Every
  capture used to be the whole app, so four rows showed four near-identical pictures and the
  detail each row argued from rendered too small to read. Each shot is now framed on its own
  claim: Meeting Edge on the guidance panel, transcripts on a speaker handover, notes on the
  generated summary and the Key Decisions table. The hero keeps the full-app view deliberately — it is the one place a
  visitor should see the whole interface, and it is what the browser-chrome frame is arguing
  for. Three rules keep this working:
  - **Crop to roughly 550–800 logical pixels wide.** That is what renders near life size in
    half a wrap. A crop of 1000+ is a stacked-layout crop and will be too small beside copy.
  - **Capture at the width the crop wants**, rather than cropping a wide window. The
    transcripts shot is taken at a 1280px viewport so the utterance bubbles wrap inside the
    frame; cropping the same column out of a 1600px capture cut every line in half.
  - **A crop must not duplicate another crop.** The transcripts shot sits on a different
    passage from anything else for this reason, and it is why the agent band ended up with
    no shot at all.

  **Which recording each shot comes from.** The notes and Meeting Edge shots come from the
  Artemis briefing. The transcripts shot comes from **"Welcome to Nojoin", the demo recording
  the product itself seeds** — four speakers trading one line each, which is what a
  diarisation shot needs and what a press conference cannot give: Artemis is long monologues,
  and its whole 12,070px transcript was scanned at 40px steps without ever holding three
  speakers at once. Any user can re-create that recording from **Settings**, or with
  `POST /api/v1/system/seed-demo`, so the shot is reproducible rather than a one-off. Note
  that the other eight seeded business meetings are metadata only and have no transcript at
  all, so they cannot supply one.
- **The selective highlight**: at most one line per page carries a flat `--action-tint` mark
  behind the text. If nothing on a page earns it, that is a finding about the page, not a
  reason to lower the bar. The highlight and the closer are separate devices; a page never
  gains a second highlight.

Tokens are shared with the app: `Base.astro` imports `frontend/src/app/tokens.css` by
relative path alongside `site/src/styles/site-tokens.css`, which holds the families the app
has no equivalent of (syntax highlighting, frame chrome, the closer-band buttons, the
marketing scale). Both go through `site/plugins/tokens-theme.mjs`, which is what makes their
dark values answer to the header toggle as well as to the OS setting. **Both must be imported
as modules from `Base.astro`.** An `@import` from `site.css` is inlined by Tailwind's pipeline
before any transform can see it, which is how the site-only tokens came to ignore the toggle
entirely; the plugin now fails the build if that import comes back. The contrast gate audits
the site as two extra themes and cannot see this class of bug at all; see DESIGN.md's
accessibility section.

## Voice

The site speaks the product's own register: plain, direct, a little opinionated, second
person, British English, no emoji. It shares a writing standard with the
sibling brand Vorkane, adapted rather than copied — Vorkane sells a person and writes in the
first person singular; Nojoin is a product and never says "I" or "we" in marketing copy.

**One page is exempt: `/managed/`.** What it sells is not the product but one named person's
time, and a service provided personally cannot honestly describe itself as "it". That page
writes in the first person singular and says so in its own first paragraph. It names nobody:
the person is identified as the one who built Nojoin, which is checkable against the commit
history, and no name goes on the site or into the repository by decision.
The exception is scoped to that page and does not travel: the landing
band that points at it stays in the product's voice and refers to "Nojoin's developer" in the
third person, so a visitor meets one narrator per page rather than two per scroll. "Developer"
rather than "author": he wrote software, not a book, and the word that describes the work is
also the word that makes the managed offering make sense. "We"
stays banned everywhere, including there — one person is an "I", never a "we", and the
corporate plural is exactly the register the rest of these rules exist to avoid.

The rules:

- **Title case for headings and eyebrow labels, sentence case for everything else.** This
  reverses an earlier sentence-case-everywhere rule. Headings on this site are labels rather
  than sentences, and title case is what the rest of the web sets them in. The style is the
  conventional one, not every-word-capitalised: principal words take a capital and short
  function words do not unless they open or close the line — "Every Word, Attributed to the
  Right Person", "£24.99 a Person, a Month", "Four Steps to Running". Body copy, leads,
  buttons, table cells and alt text stay sentence case. **The hero headline is exempt**: it is
  set as two sentences and it is baked into the Open Graph card, so changing its case means
  regenerating that card as well. "It's", "can't", "won't", "you've". The refusal to contract is
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
- **Sell, and let the facts do it.** This is a marketing site, not a paper. The register is
  confident and a little pleased with itself, and it earns that by stacking true things and
  stating them flatly rather than by reaching for adjectives: no funding, no employees, no
  bot, no caps, thirty tools, one compose file. A sentence like "Nojoin has taken no
  funding, employs nobody, and sells nothing you can't download" is doing more work than any
  superlative would, and it survives being checked, which no superlative does. The banned
  list below still holds — those words are banned because they carry no information, and
  swagger without information is just noise. Competitor claims still need a source and a
  date. Everything else is fair game.
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

**Three competitors, and which three is a decision.** The page compares Nojoin against
Jamie, Otter and Granola. Fireflies was dropped when Jamie was added rather than running a
fifth column: the detailed table is already the widest thing on the site, and another column
either squeezes every cell or pushes one somewhere nobody scrolls to. Fireflies went because
it is the least like Nojoin on the axes this page argues — bot-by-default capture, a US
cloud, no self-hosting to concede against — and Jamie is the opposite on all three, which
makes it the harder comparison and the more useful one. Jamie sits second, directly after
Nojoin, because column order decides which competitor actually gets read.

**Which competitors appear is the site owner's call.** What this document records is the
reasoning to weigh, not a rule: a set worth publishing is one where the table still contains
rows a competitor wins, because the concessions are what make the structural gaps believable.
As it stands Jamie takes an outright yes on no-bot capture and on remembering speakers, and a
partial on assistant write access — the row Nojoin leads with. A table Nojoin swept would be
worth a second look for that reason alone.

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

`/managed/` sells one thing: the developer of Nojoin scoping, installing and maintaining a
private instance for an organisation. **The page names nobody.** It writes in the first
person, because one person really does answer, but it identifies him as the person who built
Nojoin rather than by name — the full legal name stays off the site and out of the
repository by decision. The commercial shape below was settled deliberately and the
reasoning matters more than the numbers, because the numbers will move.

The tagline is "All the control, none of the admin". It replaced "Self-hosted, without
hosting it yourself", which assumed the reader already knew what self-hosting was — on the
one page most likely to be read by someone who does not. The replacement names the trade
rather than the technology.

- **The customer owns the hardware and pays for it directly.** The fee is labour and nothing
  else. That removes idle spend, cost overruns and supplier risk from the offering in one
  move, and it keeps "your server, your data" literally true — which the rest of the site
  spends two pages arguing for.
- **The page describes the machine, not its price.** 8 GB of VRAM, quoted from
  DEPLOYMENT.md, with CPU-only named as slower rather than absent. The same reasoning that
  bans competitor pricing applies to a supplier's: a quoted cloud price goes stale and a
  stale price is the error people screenshot. It is also honest that a customer with a
  suitable machine already pays nothing extra.
- **£24.99 a person a month, minimum five.** This replaced a £250 flat fee covering ten to
  twenty-five people, and a £950 setup charge waived on a twelve-month commitment. Both are
  gone from the page: per-seat is what a buyer already understands, and it scales without
  the cliff the banded version had at the sixth seat. The line "per-seat pricing runs the
  other way" went with it — the offering is now per-seat, so that argument would have been
  aimed at itself. **The price is written in two places** — the `/managed/` price card and
  the landing page's managed teaser — and the teaser was missed when the number changed, so
  the landing page advertised £250 from ten people for as long as `/managed/` sold £24.99
  from five. Move both, or the cheaper page wins the argument with the other one.
- **Nothing is capped, and the page leads on it.** No monthly allowance, no ceiling on call
  length, no history that expires while the customer is still paying. There is no meter in
  the software to hit. Every competitor has a tier where something runs out, which the
  comparison page now shows in a sourced row rather than asserting — Otter meters minutes
  below its upper plans, Granola limits history on its entry plan, and Jamie charges a
  credit a meeting and locks the notes you already have when they run out. Where a
  competitor's upper plans lift a limit, the row says so.
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

## Working on the site, and previewing it

Everything under `site/` runs in an **nvm-sourced shell**. A non-interactive shell falls back
to the distro Node and npm, which has silently rewritten `package-lock.json` before — it
strips `libc` fields and downgrades entries CI then rejects.

```bash
. ~/.nvm/nvm.sh          # Node 26 / npm 11, matching CI and the deploy job
cd site
npm ci                   # never `npm install`, for the reason above
npm run build            # writes site/dist
npm run serve            # builds, then serves the build on port 4322
```

`npm run dev` exists but preview the **built** output before asking anyone to look: the dev
server resolves imports differently and has shipped differences before.

### Sharing a preview

The site owner reviews over SSH from Windows and cannot reliably bind local ports, so a
Cloudflare quick tunnel beats `ssh -L`. It needs no account, no DNS record and no token:

```bash
~/.local/bin/cloudflared tunnel --url http://127.0.0.1:4322
```

It prints a single-use `https://<random>.trycloudflare.com` URL. `astro.config.mjs` already
allows `.trycloudflare.com` under `vite.preview.allowedHosts`; without that entry the preview
server answers every tunnelled request with a host-check error.

**There is a long-running `cloudflared` container on the development host that serves live
production traffic. Never stop, restart or reconfigure it.** It is `docker compose` service
`cloudflared` in the homelab core stack, running as uid 65532. A quick tunnel is a separate
user process and touching one has no effect on the other — but `pkill -f cloudflared` kills
both, and on this host `pkill` patterns also match the shell wrapper that invoked them. Kill
preview tunnels by PID, or by matching the binary path `~/.local/bin/cloudflared`, and tidy
them up when the review is done rather than leaving a fleet running.

### Titles: the tab and the share card are different jobs

`Base.astro` takes `title` and an optional `ogTitle` that defaults to it. They started as one
prop and were split deliberately. A browser tab wants the shortest thing that identifies the
site, so the landing page's tab reads `Nojoin` and nothing else. A Slack or LinkedIn card is
read cold by someone who has never heard of Nojoin and wants the descriptive line, so its
`og:title` stays "Nojoin — agentic meeting intelligence on your own server". Collapsing them
back into one prop degrades every share link to the bare word, invisibly from the site
itself — the same failure mode as the card that advertised a superseded tagline for two
rewrites.

Sub-pages take `Subject — Nojoin`: `Compare — Nojoin`, `Managed — Nojoin`. That keeps three
open tabs distinguishable, which flattening every page to `Nojoin` would not, and each keeps
its own longer `ogTitle`. Only set `ogTitle` on a page where the two genuinely want different
words; today that is the landing page and `/managed/`.

### The Open Graph card

`site/public/images/og-card.png` is the single card every page shares, and it carries the
hero headline. It was hand-made once and then drifted through two headline rewrites, so
every share advertised a tagline the site had stopped using — invisible from the site
itself, because nothing on the page renders it.

`site/scripts/build-og-card.mjs` regenerates it. **Run it whenever the headline changes, and
update `og:image:alt` in `Base.astro` to match.** Playwright is deliberately not a dependency
of this repository, the same call the screenshot pipeline made for a job that runs about once
a year, so the script takes a Chromium path from `PLAYWRIGHT_CHROMIUM` and expects
`playwright-core` to be resolvable from outside the repo.

The card's colours are the app's dark-theme tokens, quoted in the script rather than imported:
it renders a bitmap, out of the stylesheet's reach. No gate catches a drift between the two.

### What a static preview cannot show you

`/docs/*` redirects do not fire. They live in `site/worker/index.js` and only run on a real
Workers deployment, so a 404 on `/docs/TELEMETRY` in local preview is expected rather than a
regression. `wrangler dev` is not parity either — redirect rules that worked there have failed
in production. Verify that contract against `https://www.nojoin.co.uk/docs/TELEMETRY` after a
deploy; there is no longer a workers.dev copy to test it on first, so a redirect change is
verified in production or not at all.

### Checks worth running before asking for review

- `node frontend/scripts/check-contrast.mjs` — audits the site as two of its four themes.
  It only checks pairings listed in `SITE_PAIRINGS`, so **new colour-on-colour furniture is
  invisible to it until someone adds the pairing**. Adding one is part of adding the
  furniture, not a follow-up. It also measures values only, never whether the rule holding
  them applies to anybody, so a green gate is not evidence that a visitor can reach the
  colours it just approved.
- **The four-way theme matrix.** The site has two theme inputs — the OS
  `prefers-color-scheme`, and the choice the header toggle stores in `localStorage` under
  `nojoin-theme` — and the bugs live where the two disagree. Checking "light" and "dark" is
  checking half of it. Force both independently: Playwright's `colorScheme` context option
  for the OS, and an init script setting `nojoin-theme` for the choice.

  | Stored choice | OS setting | What must render |
  | --- | --- | --- |
  | none | light | light |
  | none | dark | dark |
  | light | dark | light |
  | dark | light | dark |

  The last two are the ones that shipped broken. Read the computed value of a site-only
  token (`--code-fg` is the clearest) rather than judging by eye: the page chrome comes from
  the app tokens and looked right in every case while the syntax colours were wrong.
- Horizontal overflow at 360px and 1920px, in both themes, on every page: compare
  `document.documentElement.scrollWidth` against `window.innerWidth`. Two overflow bugs have
  shipped from this repository and both were invisible at desktop width. An element with its
  own `overflow-x: auto` exceeding its box is fine — a code block and the comparison tables
  both do, deliberately. The document moving is not.
- `python3 scripts/validate_docs.py` and `git diff --check`.

## How it is served, and what replaced what

`www.nojoin.co.uk` is a Cloudflare **Worker Route**, declared in `site/wrangler.jsonc` and
deployed by CI. It is a Route rather than a Custom Domain on purpose: the hostname was
already a proxied record, a Custom Domain cannot attach to a hostname that has one without
deleting the record first, and that would put DNS propagation behind any rollback. A Route
sits in front of the proxied record, so adding it intercepts traffic atomically and removing
it stops intercepting just as atomically.

**The site is served on that route alone.** `workers_dev` was `true` while the site was
being built, so it could be reviewed before the route existed, and it left a complete public
duplicate of every page on `nojoin-site.taylan-d.workers.dev`. It is now `false`. The
duplicate had no remaining job, and a second indexable copy of a marketing site is a
liability: `Base.astro` builds its canonical from `Astro.site` and so pointed crawlers back
at `www.nojoin.co.uk`, but that is mitigation rather than a reason to keep it. Reviewing an
unmerged change is what `npm run serve` and a quick tunnel are for.

**Rolling back is `git revert` of the commit that added the route, then letting CI deploy.**
Deleting the route in the Cloudflare dashboard is not the rollback path: the weekly scheduled
deploy from `main` re-applies whatever `wrangler.jsonc` says.

That rollback used to land on a live GitHub Pages origin. It no longer does. Pages served
the previous Jekyll site from `main` as a `build_type: legacy` build — no workflow, just a
repository setting — and it was disabled and its sources deleted (`_config.yml`, `_layouts/`,
`_includes/`, `assets/css/style.scss`, `docs/index.md`, `CNAME`) when the Astro site went
live. `assets/images/nojoin-mark.svg` stayed, because `README.md` still references it.

The consequence is worth being clear about: reverting the route now removes the Worker and
leaves the DNS record pointing at nothing, so **revert is no longer a fallback to a working
site — it is a way to take the site down.** Fixing a bad deploy means rolling `site/` forward,
not backwards. Re-enabling Pages is a repository settings change that no `git revert` can
perform.

Cloudflare Web Analytics is wired through `PUBLIC_CF_BEACON_TOKEN`, a build-time environment
variable read in `Base.astro` and supplied by CI as a **repository variable, not a secret**.

The value is the beacon `siteTag`. It is not an API token: it has no scopes, grants nothing,
and ships in the markup of every page that uses Web Analytics, so anyone can read it from any
such site. **A Cloudflare API token must never be used here** — an "Analytics Read" token is a
real credential and putting one in a public repository is a leak that needs rotating.

It comes from the environment rather than from source for cleanliness rather than secrecy. A
build-time value is inlined into the HTML either way, so an environment variable hides
nothing from anyone. What it does is keep the beacon out of local builds: a source constant
would fire real page views from every `npm run build` and every preview tunnel, tagged with a
throwaway `trycloudflare.com` hostname, straight into the live figures. Unset is a supported
state and renders no beacon at all, which is what every local build should get.

## Maintenance

- The site deploys from `main` only: a push touching `site/`, a published release, a weekly
  schedule, and manual dispatch.
- Build-time data (star count, latest release) degrades gracefully: the release falls back
  to `docs/VERSION`, the star count to no number. No API outage may fail a build.
- Screenshots are real captures from a seeded demonstration instance processed through the
  genuine pipeline, in light and dark pairs swapped by `<picture>`. The source audio is
  public-domain material, labelled honestly with real titles and speakers; no invented
  names sit over real people's words.

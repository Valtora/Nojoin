# Flat UI restyle and folded UX pass

Working plan for branch `feat/flat-ui-restyle`. This document is a PR-scoped artefact and is
removed in the final commit before merge.

## 1. Purpose

Replace Nojoin's gradient/glass visual language with a modern flat style across the web app and
the marketing site, while keeping the existing colour palette. A contrast audit to WCAG 2.2 AA is
in scope and enforced going forward by a CI check. The previously planned People reflow and
mobile-wide UX pass are folded into this work and land per-surface alongside the restyle.

The current aesthetic is implemented as: ambient radial-plus-linear gradient page backgrounds
(`AmbientWorkspace`, duplicated inline in the recording detail page), gradient nav rails
(`MainNav`, `Sidebar`), ~35 hand-rolled modal scrims with `backdrop-blur`, ~10 translucent
glass chrome surfaces, heavy `shadow-xl/2xl` usage, and a marketing site built on stacked radial
glows and blurred glass chrome. There is no shared primitive layer (405 raw `<button>` elements,
no Modal/Card/Input components outside settings), and colour is applied through ~5,900 hard-coded
Tailwind palette utilities with ~2,800 `dark:` branches.

## 2. Decisions already made

These were resolved with the maintainer before this plan was written. They are fixed inputs, not
open questions.

| # | Decision |
| --- | --- |
| 1 | Scope is the Next.js app and the Jekyll marketing site, in one project. |
| 2 | The flat canon is the settings-card style: solid surfaces, 1px hairline borders, 4%-alpha resting shadow in light, no shadow in dark, separation by lightness. |
| 3 | A full primitive set is built: Button, IconButton, Modal, Card, Badge/StatusBadge, Input, Select. |
| 4 | Full re-tokenisation: all hard-coded colour utilities move to semantic tokens; `dark:` colour branches collapse into token definitions. |
| 5 | Nav rails become flat solid: warm cream tint in light, solid deep navy (~`#0b1220`) in dark, hairline borders, no gradients. |
| 6 | Marketing keeps its navy canvas but goes flat: radial washes, backdrop blur, and glow shadows are removed. Marketing typography moves to Geist. Marketing's `#ff6a13` orange is retired in favour of the app's Tailwind orange ramp. |
| 7 | The brand gradient (`#ffb31a → #ff6a13 → #ff3e2f`) survives only inside the logo assets. No gradient text, fills, bars, or buttons anywhere. |
| 8 | Floating elements (modals, dropdowns, popovers, toasts) keep a moderate shadow in both themes, as the single documented exception to the no-dark-shadow rule. |
| 9 | Modal scrims are a plain dim with no blur. Sticky chrome (TopBar, RecordingHeader, AudioPlayer, floating mobile buttons) becomes fully solid. No `backdrop-filter` remains anywhere. |
| 10 | Contrast target is strict WCAG 2.2 AA (4.5:1 for all text including button labels, 3:1 for large text, UI component boundaries, and meaningful icons). Consequence: the light-mode primary action and orange link text darken from orange-600 to orange-700, hover moves to orange-800. A scripted token contrast check is added to CI. |
| 11 | Radii tighten one step via the density tokens: cards/controls stay 8px, panels ~16px, the 32px surface radius drops to ~20px, marketing pills become rounded rectangles. |
| 12 | Verification is a manual matrix (surface × light/dark × comfortable/compact × key widths) with before/after screenshots per phase. No Playwright/axe harness is built. |
| 13 | Delivery is one long-lived branch, merged once. The People page full reflow and the mobile-wide UX pass ride along, applied per-surface. |
| 14 | `fix/mobile-responsive-ui` (8 unmerged commits, 52 behind main) is mined for intent and then retired, not merged. |
| 15 | `docs/DESIGN.md` is rewritten as part of this work. |

## 3. Non-goals

- No change to the colour palette's identity: orange remains the brand, the grey/slate neutrals
  remain, status hues keep their meanings. Only contrast-driven step shifts are permitted.
- No logo changes. Assets in `frontend/public/assets/` and `assets/images/` are untouched.
- No behavioural backend changes. This is frontend, marketing CSS, docs, and one CI script.
- No new accessibility tooling beyond the contrast script (no axe, no jsx-a11y, no Playwright).
- No marketing copy rewrites; `docs/index.md` content changes only where styling markup demands.
- The telemetry dashboard repository is out of scope.
- No timeline commitments; phases below are an ordering, not a schedule.

## 4. Target design specification

### 4.1 Design language rules

The canon generalises `.settings-card` to the whole product:

1. Every resting surface is a solid fill with a 1px hairline border. Light mode adds the 4%-alpha
   resting shadow (`--surface-card-shadow`); dark mode has none and separates by lightness.
2. The surface stack stays exactly two levels deep (page, card), with `--surface-inset` for
   composite UI inside a card. This rule already exists in `globals.css` and is unchanged.
3. Floating elements (anything rendered over other content: modals, dropdowns, popovers, toasts,
   the tour highlight) carry a moderate shadow in both themes. One token defines it.
4. No gradients outside the logo assets. No `backdrop-filter` anywhere. No translucent surface
   fills except scrims and the inset token's deliberate low-alpha values.
5. Interactive affordance moves from "shadow lifts on hover" to flat conventions: background
   tint shifts, border emphasis, and colour steps. Cards that are clickable darken/tint their
   border and background on hover instead of raising a shadow.
6. Focus rings stay orange, 2px, on all interactive elements, via a single token.
7. Motion rules are unchanged (quick 0.15–0.3s transitions, no bounce, reduced-motion respected),
   minus any transition whose only job was animating a shadow lift.

### 4.2 Token architecture

All tokens live in `frontend/src/app/globals.css`. Tailwind v4's CSS-first config means each
`--color-*` entry in `@theme inline` yields utilities (`bg-*`, `text-*`, `border-*`) with no
config file. The pattern is: raw values on `:root` and `.dark`, referenced with `var()` from
`@theme inline` so utilities are theme-reactive.

The existing families (`--contrast-*`, `--surface-*`, density/layout tokens) are kept and
extended rather than renamed, to avoid churning the ~200 existing usages and the documentation.
New families added:

**Action (brand):**

| Token | Light | Dark | Notes |
| --- | --- | --- | --- |
| `--action` | orange-700 `#c2410c` | orange-500 `#f97316` | Fills, links, active states. AA vs white/near-black. |
| `--action-hover` | orange-800 `#9a3412` | orange-400 `#fb923c` | |
| `--action-on` | white | near-black | Text/icon on an action fill. |
| `--action-tint` | orange-100 | orange-500 at low alpha | Selected/active row tint. |
| `--action-tint-fg` | orange-800/900 | orange-200 | Text on the tint. AA-checked. |
| `--focus-ring` | orange-500 | orange-400 | Focus outline only; 3:1 non-text requirement. |

Exact dark values are tuned during Phase 1 against the contrast script; the light values above
are fixed by decision 10.

**Rails:** `--rail-bg` (warm cream tint / `#0b1220`), `--rail-border`, `--rail-fg`,
`--rail-fg-muted`, `--rail-item-hover`, `--rail-item-active`, `--rail-item-active-fg`.

**Status** (five states: queued/processing, generating-notes, paused, success, error), each with
`-bg`, `-fg`, and `-border` in both themes. Light mode keeps tint-background/dark-text pills;
dark mode keeps translucent-fill/light-text pills. Every pair must pass 4.5:1. The existing
`--settings-tab-*` tokens are re-expressed on top of the action family where they coincide.

**Overlay/float:** `--scrim` (plain dim, tuned around black/50–60), `--float-shadow` (both
themes), plus a small fixed z-index scale to replace the current spread (`z-40` → `z-99999`):
`--z-sticky`, `--z-dropdown`, `--z-modal`, `--z-toast`, `--z-tour`. The app-wide convention
today is `z-9999` for modals; the new scale replaces all variants through the primitives.

**Neutral text/border/surface:** already exist (`--foreground`, `--contrast-*`, `--surface-*`).
Gaps found during the sweep (e.g. a disabled-text token, a table-stripe token) are added to these
families rather than invented ad hoc.

Re-tokenisation rule: after the sweep, a component file contains no raw Tailwind palette colour
utilities (`gray-*`, `orange-*`, `red-*`, …) and no `dark:` colour branches. `dark:` remains
legitimate for non-colour concerns only (e.g. an image swap). The allowlist of files that may
contain raw palette values is: `globals.css` (token definitions), `driver-theme.css` (until
Phase 3 converts it), and test fixtures.

### 4.3 Radius scale

Token-only change: `--surface-radius` 2rem → 1.25rem, `--surface-radius-subtle` 1.75rem →
1.125rem, `--surface-radius-panel` 1.5rem → 1rem, with compact-density values shrinking
proportionally. Cards and controls stay at `rounded-lg` (8px) equivalents. Arbitrary radius
values (`rounded-[1.75rem]` etc.) are migrated onto the tokens during the sweep. Pills remain
for genuinely pill-shaped elements (tags, status badges, avatars); marketing chrome pills become
rounded rectangles.

### 4.4 Typography and iconography

App typography is unchanged (Geist / Geist Mono via `next/font`). Marketing drops Sora and
Manrope for Geist loaded from Google Fonts in `_includes/head-custom.html` (the Jekyll site has
no Next font pipeline). Lucide remains the sole icon system; the legacy bespoke PNG action icons
are replaced with Lucide equivalents where they are touched by the sweep.

## 5. Primitive component set

New primitives live in `frontend/src/components/ui/` beside the existing widgets. All consume
tokens exclusively. Variant plumbing uses the existing `cn()` helper; no CVA dependency is added
unless it proves clearly simpler during Phase 1.

| Primitive | Replaces | API sketch |
| --- | --- | --- |
| `Button` | 405 raw `<button>`s; 86 hand-typed `bg-orange-600` sites | `variant: primary / secondary / ghost / danger`, `size: sm / md / lg`, `loading`, `icon` slots. Primary = action fill; secondary = bordered surface; danger = error family. |
| `IconButton` | ad hoc square icon buttons (toolbars, card actions) | `size`, `variant`, required `aria-label`. Enforces the ≥40px touch target from the mobile pass. |
| `Modal` | ~25 divergent `fixed inset-0 … bg-black/50 backdrop-blur-sm` scrims | Portal, `--scrim` overlay (no blur), `--z-modal`, focus trap, escape/overlay close, `size`, mobile gutter + `max-h` cap (mined from the modal-caps commit). All existing modals migrate onto it. |
| `Card` | the canonical surface, hand-rolled everywhere | `interactive` prop switches hover affordance (border/tint, not shadow). Wraps the settings-card recipe. |
| `Badge` / `StatusBadge` | hand-rolled pills; unifies with `SettingsStatusBadge` | `StatusBadge` maps recording status → status tokens + Lucide glyph (spinner for processing). |
| `Input`, `Select` | scattered form fields, the duplicated `SELECT_CLASS` constants | Shared field chrome: border, focus ring, disabled, error state, mono option. `Switch` and `Tooltip` already exist and are restyled onto tokens. |

`AmbientWorkspace` is retired. Its replacement is a trivial flat workspace wrapper (page
background + existing `workspace-shell` layout classes); the duplicated gradient string in
`recordings/[id]/page.tsx` is deleted in the same change.

## 6. Contrast audit and CI enforcement

A standalone Node script, `frontend/scripts/check-contrast.mjs`, run as
`npm run check:contrast`:

1. Parses the `:root` and `.dark` blocks of `globals.css` (and the marketing SCSS variable block)
   for token values, resolving `var()` indirection and flattening alpha over the relevant
   backdrop (a translucent fill is composited over its documented backdrop before measuring).
2. Checks a declared list of foreground/background pairings per theme, kept in the script beside
   the tokens it tests: text pairs at 4.5:1, large-text/UI-component/border-vs-adjacent pairs at
   3:1. The pairing list is the audit's artefact; a pairing that cannot be expressed as tokens is
   a smell to fix in the tokens.
3. Exits non-zero listing every failing pair with computed ratios.

Wiring: a script step appended to the existing `Frontend lint` CI job (it triggers on
`frontend/**`, which covers both the script and `globals.css`), and to the local validation
habit (`npm run lint && npm run test && npm run build`). Phase 1 runs the initial audit and fixes
every failing token before any surface work begins, so the sweep never propagates a failing pair.

## 7. Phase plan

Work happens in this order on the single branch, one reviewable commit (or small commit set) per
phase, so history stays inspectable even though it merges once. `npm run lint`, `npm run test`,
and `npm run build` gate every phase that touches `frontend/`.

### Phase 0 — Baseline

- Capture "before" screenshots of every surface in the verification matrix (§9), both themes,
  both densities, at 375px and desktop widths. Stored outside the repo (PR description assets).
- Record the mechanical baseline counts (gradient, blur, shadow, palette-class occurrences) so
  the final gates in §10 can show a clean delta.

### Phase 1 — Foundation

- Add the token families of §4.2 and the radius changes of §4.3 to `globals.css`.
- Build the primitives of §5, plus restyle the existing `ui/` widgets (`Switch`, `Tooltip`,
  `ModernDatePicker`, `MultiSelect`) onto tokens.
- Write `check-contrast.mjs`, run the initial audit, fix failing tokens, wire into CI and
  `package.json`.
- Rewrite `docs/DESIGN.md` to describe the target system (tokens, primitives, flat canon, the
  floats exception, the contrast gate). It is the contract the sweeps are reviewed against.

### Phase 2 — Shell

- `MainNav`, `Sidebar`: flat solid rails on the rail tokens, gradient removal, hover/active
  states per §4.1. Sidebar is 1,039 lines and also receives its mined mobile fixes here
  (`h-dvh`, drawer behaviour).
- `TopBar`: solid, in-flow sticky bar (mined pattern), hairline bottom border.
- Retire `AmbientWorkspace` (and the duplicate gradient in `recordings/[id]/page.tsx`) for the
  flat workspace wrapper.
- Migrate every modal onto the `Modal` primitive. This is the z-index normalisation and the
  scrim change in one motion, and carries the mined modal `max-h`/gutter caps.
- `(dashboard)/layout.tsx`, `loading.tsx`, auth pages (`login`, `register`), setup wizard steps,
  and the OAuth consent page: flatten and tokenise (they are small and shell-adjacent).

### Phase 3 — Surface sweeps

Each surface gets, in one commit: re-tokenisation (no palette classes, no `dark:` colour
branches), migration onto primitives, gradient/glass/shadow removal, and its folded UX items.
Order chosen so patterns stabilise on simpler surfaces first:

1. **Tasks** — `TasksWorkspace` (densest colour file), `TaskRow` (gradient card, double-click
   edit affordance, ≥40px action targets from the mined work).
2. **Dashboard home** — `DashboardHome`, `DashboardUpcomingMeetingsCard`, `CalendarCards`,
   `MonthCalendar`, `DashboardTasksPanel`.
3. **Recordings landing** — `RecordingsLanding`, `RecordingCard`, `BatchActionBar`. Context-menu
   changes here must be mirrored in `Sidebar.tsx` per the repo rule.
4. **Recording detail** — the largest surface: `RecordingHeader` (glass → solid),
   `RecordingMainContent`, `TranscriptView`, `NotesView`, `SpeakerPanel`, `ChatPanel`,
   `AudioPlayer` (glass → solid), `DocumentsView`, `MeetingEdgePanel`, TipTap/ProseMirror
   content styles in `globals.css`. Folded UX (mined): mobile Speakers as a 4th tab
   (`grid-cols-3` → `grid-cols-4`, mount `SpeakerPanel` in the mobile branch), `NotesView`
   toolbar overflow fixes mirroring `TranscriptView`, `DocumentsView` touch-reachable delete.
5. **Live capture** — `CaptureShell`, `LiveMeetingControls`, `LiveTranscriptPanel`,
   `LiveAudioWaveform` (gradient bars → flat fills), `RecordingStatusDisplay` (gradient progress
   → flat action fill), `RecordingFloatingBadge`, `CaptureSettings` meters. Capture-adjacent
   changes run `npm run test -- --run src/lib/capture` and get manual smoke steps (§9).
6. **People (full reflow)** — `people/page.tsx`, `PeopleTable` (table → card stack below `lg`,
   mined), `PeopleTagSidebar` (touch-accessible tree controls, explicit rename button, mined),
   `PersonModal`, `PeopleFilters`, batch/recalibrate/split modals.
7. **Settings** — verify the landed IA redesign against the canon and finish the leftovers:
   any remaining off-brand controls, hand-rolled pills → `StatusBadge`, `SELECT_CLASS`
   deduplication into `Select`, z-index conformance via `Modal`. Most mined settings commits are
   expected to be obsolete; each is checked against main before reimplementing.
8. **Cross-cutting stragglers** — `NotificationToast`, `NotificationHistoryModal`,
   `ServiceStatusAlerts`, `TelemetryNotice`, `TourGuide`/`driver-theme.css`,
   `react-datepicker` theming, export/info modals not already migrated.

The mobile-wide UX pass is distributed into these items; anything app-wide rather than
per-surface (breakpoint drift onto the `lg` boundary, remaining fixed-viewport `h-screen`/vh
traps, hit-target sizing) is checked as part of each surface's commit rather than as a separate
phase.

### Phase 4 — Re-tokenisation completion

The long tail: repo-wide sweep to zero for the mechanical gates in §10. This phase exists so
Phase 3 commits can stay surface-scoped without chasing every last occurrence, and it is where
the `dark:` colour-branch collapse is verified file by file.

### Phase 5 — Marketing site

Rewrite `assets/css/style.scss` on the same visual system: solid navy canvas, no radial washes,
no `backdrop-filter`, no glow shadows, flat cards with hairline borders, moderate float shadow,
Geist via `_includes/head-custom.html`, orange unified to the app ramp (marketing runs on its
dark canvas, so the dark-theme action values apply), radii per §4.3, pills → rounded rects.
`_layouts/default.html` markup adjusted only as the styles require. The marketing token block is
added to the contrast script's parse list.

### Phase 6 — Close-out

- Execute the full verification matrix (§9); capture "after" screenshots.
- Final docs pass: `DESIGN.md` corrections found during the sweeps; `docs/SCREENSHOTS.md`
  imagery re-captured; `USAGE.md` touched only if a folded UX change altered a described flow.
- Delete this plan document.
- After merge (outside the repo): reconcile the `nojoin-design` skill with the shipped system
  and delete `fix/mobile-responsive-ui` locally and on origin.

## 8. Mining reference: `fix/mobile-responsive-ui`

The branch is reference material only; nothing is merged or cherry-picked wholesale, because it
is 52 commits behind and predates the settings IA redesign. Intents to reimplement are folded
into the phases above: sticky in-flow TopBar, `h-dvh` shell, People card stack and tag-tree
touch controls, modal `max-h`/gutter caps, mobile Speakers tab, `NotesView` toolbar fixes,
`DocumentsView` touch delete, task/chip hit targets, waveform bar clipping. Known corrections to
honour: the MeetingEdge slider-label overlap was a false positive (labels 1–3 are empty); the
settings-file commits are presumed obsolete and are re-judged against main individually. The
branch is deleted at close-out.

## 9. Verification

Automated, per phase touching the frontend: `npm run lint`, `npm run test`, `npm run build`;
`npm run check:contrast` once it exists; `npm run test -- --run src/lib/capture` when capture
surfaces change. The full pytest suite is not needed (no backend changes); CI's `detect-changes`
will gate accordingly.

Manual matrix, executed per phase for the surfaces that phase touched and in full at close-out:

- **Surfaces:** login/register, setup wizard, OAuth consent, dashboard home, recordings landing,
  recording detail (transcript, notes, speakers, chat, documents, audio player, Meeting Edge),
  live capture (start → pause/resume → stop, waveform, floating badge, live transcript), tasks,
  people (table and card stack, tag sidebar, modals), settings (spot-check across the 14
  categories), notifications/toasts, tour, marketing site pages.
- **Axes:** light and dark × comfortable and compact density × 375px, 768px, and ≥1024px widths.
- **Capture smoke** (when touched): share picker, mic capture, waveform/live status, pause/
  resume, stop/finalise, discard, unsupported-browser messaging, on Chromium.
- Before/after screenshots accompany each phase's work for review in the eventual PR.

## 10. Definition of done (mechanical gates)

All of the following hold over `frontend/src/` (and `assets/css/` where marked) at merge:

1. Zero `gradient` utilities or `linear-gradient`/`radial-gradient` values, app and marketing,
   excluding logo image assets.
2. Zero `backdrop-blur`/`backdrop-filter`, app and marketing.
3. Zero raw Tailwind palette colour utilities outside the §4.2 allowlist; zero `dark:` colour
   branches in components.
4. `shadow-*` utilities appear only inside the primitives (float shadow) and token definitions.
5. All modals render through the `Modal` primitive; z-index values come only from the z-scale
   tokens.
6. `npm run check:contrast` passes both themes plus the marketing block, and is wired into CI.
7. `npm run lint`, `npm run test`, `npm run build` green; capture tests green.
8. `docs/DESIGN.md` describes the shipped system; this plan file is deleted.
9. The verification matrix has been executed in full with no open regressions.

## 11. Phase 0 baseline

Recorded on the branch point, before any restyle work, so the §10 gates can be reported as a
delta rather than as an absolute. Counts are occurrences unless stated; the app column measures
`frontend/src/`, marketing measures `assets/css/`, `_layouts/`, `_includes/`, `docs/index.md`.

| Measure | Baseline | Gate target |
| --- | --- | --- |
| Gradient utilities (`bg-gradient-to-*`, `from/via/to-*`) | 28 in 9 files | 0 |
| `linear-gradient` / `radial-gradient` CSS values (app) | 22 | 0 |
| `backdrop-blur` utilities | 55 in 46 files | 0 |
| `backdrop-filter` declarations (app) | 1 | 0 |
| `shadow-*` utilities, all | 233 in 83 files | primitives and tokens only |
| of which `shadow-xl` / `shadow-2xl` | 57 | 0 |
| of which `shadow-orange-*` glows | 27 | 0 |
| Raw Tailwind palette colour utilities | 6,141 in 138 files | 0 outside the §4.2 allowlist |
| `dark:` variants, all | 2,921 | non-colour concerns only |
| of which `dark:` colour branches | 2,476 | 0 |
| Raw `<button>` elements | 405 in 102 files | primitives only |
| `fixed inset-0` scrims | 35 | `Modal` primitive only |
| `z-*` utility occurrences | 79, across 10 distinct values | z-scale tokens only |
| Distinct `z-*` values | `z-0 z-10 z-20 z-30 z-40 z-50 z-100 z-9999 z-99999 z-999999` | the 5 z tokens |
| Arbitrary radii `rounded-[…]` | 10 | 0 |
| Marketing `linear`/`radial-gradient` | 20 | 0 |
| Marketing `backdrop-filter` | 1 | 0 |
| Marketing `box-shadow` declarations | 9 | float shadow only |
| Marketing Sora/Manrope references | 7 | 0 |
| Marketing `#ff6a13` | 2 | 0 |

Two figures correct §1's estimates: `backdrop-blur` is 55 occurrences across 46 files, not ~35
(35 is the count of `fixed inset-0` scrims specifically), and the shadow load is 233 utilities,
not only the 57 heavy ones. The z-index spread also reaches `z-999999`, wider than the `z-40` to
`z-9999` range recorded in §4.2.

Gradient-bearing files (9): `recordings/[id]/page.tsx`, `RecordingStatusDisplay`,
`LiveAudioWaveform`, `AmbientWorkspace`, `MainNav`, `Sidebar`, `people/RecalibrateModal`,
`settings/CaptureSettings`, `dashboardTasks/TaskRow`.

Toolchain note: the repo's Node floor is 22 and CI builds on 26. The baseline gates
(`npm run lint`, `npm run test`, `npm run build`) were confirmed green on Node 26.5.1 at this
commit: 52 test files, 335 tests passing.

## 12. Risks

- **One long-lived branch, no freeze rules** (accepted): anything landing on main mid-flight
  (Dependabot lockfile bumps included) must be merged into this branch manually; the final merge
  is the costliest step. Mitigation is the phase ordering itself: history stays reviewable, and
  the branch is always buildable so a partial land remains possible if priorities change.
- **Full re-tokenisation blast radius**: ~130 files change colour classes; manual verification
  is the only visual net. The per-surface commit discipline and the matrix are the mitigations.
- **Strict AA changes the CTA colour**: light-mode primary shifts one ramp step; this is a
  deliberate, user-approved brand change but will be the most visible diff to end users.
- **Vendor CSS edges**: TipTap/ProseMirror content, `react-datepicker`, and driver.js theming
  do not consume Tailwind utilities directly and need hand-written token-based overrides;
  these are called out in Phases 3 and 4 so they are not discovered late.
- **Settings double-work**: the mined settings fixes may already be superseded by the landed IA
  redesign; each is verified against main before any reimplementation to avoid regressing newer
  work.

# Design System

The web client's visual language is defined once, as CSS custom properties in
[frontend/src/app/tokens.css](../frontend/src/app/tokens.css) (imported by `globals.css`, which
keeps the app-only furniture), and consumed through Tailwind
utility classes and a small set of primitive components. The marketing site runs the same system on
its own dark canvas; see [The marketing site](#the-marketing-site) below. This document records the
tokens, the primitives, and the rules that govern them, so a change to the interface can be checked
against an intended system rather than against whatever the surrounding component happened to do.

## The flat canon

Every resting surface is a solid fill with a 1px hairline border. There are no gradients, no
translucent glass, and no `backdrop-filter` anywhere in the product. The brand gradient survives
only inside the logo image assets.

1. **Two surface levels, and no more.** The page, and one card on it. `--surface-inset` exists for
   composite UI inside a card, such as meters and consoles, and steps in the same direction. No
   card ever contains another card.
2. **Light separates by shadow, dark separates by lightness.** Light mode adds a 4%-alpha resting
   shadow (`--surface-card-shadow`); dark mode resolves that token to `none`, because a shadow
   against a dark page reads as a smudge rather than as elevation.
3. **Floats are the one exception.** Anything rendered over other content, meaning modals,
   dropdowns, popovers, tooltips and toasts, carries `--float-shadow` in *both* themes. It has to:
   in dark mode a scrim cannot darken a page that is already dark, so the panel rises
   instead. Floats also get their own surface token, `--surface-float`, which is lighter than a
   card in dark mode. This is not a third level of the surface stack; an overlay sits on a
   different axis to the page and card it covers.
4. **Affordance is colour, not elevation.** A clickable card tints its background and strengthens
   its border on hover. Nothing lifts. Shadow-on-hover never worked in dark mode anyway, where
   there is no resting shadow for a hover state to differ from.
5. **Scrims are a plain dim.** `--scrim`, no blur. Sticky chrome is fully solid.
6. **Focus rings are orange, 2px, on every interactive element**, from `--focus-ring`.
7. **Motion stays quick and flat**: 0.15 to 0.3s transitions, no bounce, reduced motion respected.
   No transition exists whose only job is animating a shadow.

## Themes

Light and dark are both first-class. Dark mode is class-based rather than media-query-based, so a
user can override their operating system preference:

```css
@custom-variant dark (&:where(.dark, .dark *));
```

Raw values are declared on `:root` and overridden on `.dark`; `@theme inline` then references them
through `var()`, so every generated utility is theme-reactive. Custom properties cascade, so `.dark`
declares only what actually changes.

**A component must not branch on the theme.** A `dark:` variant carrying a colour is a bug: it means
a token is missing. `dark:` remains legitimate for non-colour concerns, such as swapping an image.

**`color-scheme` is declared on both themes, and it is not optional.** Native controls are drawn by
the browser: no selector in this stylesheet reaches inside an open `<select>` popup, a checkbox, a
radio, a range thumb or a date picker. `color-scheme` is the only lever that tells the browser which
of its own renderings to use, so without `light` on `:root` and `dark` on `.dark`, every native
control renders light while the rest of the app is dark. This is the price of `Select` being a
native select, and it is worth paying for the platform picker on a phone.

## Colour

### Action, the brand

| Token | Light | Dark | Purpose |
| --- | --- | --- | --- |
| `--action` | orange-700 `#c2410c` | orange-700 `#c2410c` | Primary fills |
| `--action-hover` | orange-800 `#9a3412` | orange-800 `#9a3412` | |
| `--action-active` | orange-900 `#7c2d12` | orange-900 `#7c2d12` | |
| `--action-on` | `#ffffff` | `#ffffff` | Label on an action fill |
| `--action-text` | orange-700 `#c2410c` | orange-400 `#fb923c` | Links, accents, active text |
| `--action-text-hover` | orange-800 `#9a3412` | orange-300 `#fdba74` | |
| `--action-tint` | orange-100 `#ffedd5` | orange-500 at 18% | Selected or active row |
| `--action-tint-hover` | orange-200 `#fed7aa` | orange-500 at 26% | |
| `--action-tint-fg` | orange-800 `#9a3412` | orange-200 `#fed7aa` | Text on the tint |
| `--action-border` | orange-300 `#fdba74` | orange-400 at 35% | Tint outlines |
| `--focus-ring` | orange-600 `#ea580c` | orange-400 `#fb923c` | Focus outline only |

Two things here are not obvious.

**The fill is orange-700, not the orange-600 this product used to use.** White on orange-600 is
3.56:1, short of the 4.5:1 held to for all text including button labels. This is the most visible
change the restyle makes to the interface.

**`--action` and `--action-text` are separate tokens.** One colour cannot do both jobs, because a
fill is judged by the label on it while text is judged by the surface under it, and in dark mode
those pull in opposite directions: the orange-700 that carries a white label at 5.18:1 reads as
text on the dark page at only 3.49:1. They coincide in light mode and diverge in dark, which is
the entire reason the split exists. Use `--action` for something filled, `--action-text` for
something written.

`--focus-ring` is the one member of the family allowed to stay lighter, because it is never a text
colour. It answers to the 3:1 non-text threshold instead.

### Danger

Shaped like the action family so the two behave the same way. Red-600 is the one step that carries
a white label at 4.83:1 and still separates from both a white page and a dark one, so unlike
the brand the fill needs no per-theme value.

| Token | Light | Dark |
| --- | --- | --- |
| `--danger` | red-600 `#dc2626` | red-600 `#dc2626` |
| `--danger-hover` | red-700 `#b91c1c` | red-700 `#b91c1c` |
| `--danger-active` | red-800 `#991b1b` | red-800 `#991b1b` |
| `--danger-on` | `#ffffff` | `#ffffff` |
| `--danger-text` | red-700 `#b91c1c` | red-400 `#f87171` |
| `--danger-text-hover` | red-800 `#991b1b` | red-300 `#fca5a5` |

### Contrast tokens

These keep text and boundaries legible against both backgrounds. Prefer them over raw Tailwind
grey scales for body-adjacent text and borders.

| Token | Light | Dark |
| --- | --- | --- |
| `--foreground` | `#171717` | `#ededed` |
| `--contrast-muted` | `#374151` | `#e5e7eb` |
| `--contrast-helper` | `#4b5563` | `#d1d5db` |
| `--contrast-icon-muted` | `#52525b` | `#a1a1aa` |
| `--contrast-border` | `#cbd5e1` | `#4b5563` |
| `--contrast-border-strong` | `#94a3b8` | `#6b7280` |

The light and dark values are not mirror images. Dark mode lifts muted text well above its
light-mode equivalent, because grey text loses legibility faster on a dark background.

### Surfaces

| Token | Light | Dark |
| --- | --- | --- |
| `--surface-page` | `#f8fafc` | `#161616` |
| `--surface-card` | `#ffffff` | `#202020` |
| `--surface-card-border` | `rgba(203, 213, 225, 0.58)` | `rgba(75, 85, 99, 0.38)` |
| `--surface-divider` | `rgba(203, 213, 225, 0.42)` | `rgba(75, 85, 99, 0.25)` |
| `--surface-card-shadow` | `0 1px 2px 0 rgb(15 23 42 / 0.04)` | `none` |
| `--surface-inset` | `rgba(241, 245, 249, 0.75)` | `rgba(255, 255, 255, 0.03)` |
| `--surface-float` | `#ffffff` | `#2a2a2a` |
| `--surface-float-border` | `rgba(203, 213, 225, 0.9)` | `rgba(148, 163, 184, 0.22)` |

The dark canvas is a soft charcoal rather than a near-black, and the hairlines sit close to the
1.15:1 house floor in both themes. Both are deliberate: a border heavy enough to read as a line
turns a page of cards into a wireframe, and the 4% shadow, not the border, is what lifts a card in
light mode.

The three surfaces have to move together, because `--action` clears the 3:1 a resting fill owes by
only 3.15:1 against the dark card. Lightening the card alone would fail the audit.

### Controls

`--control-border` is deliberately separate from `--contrast-border`, because the two answer to
different rules. A control's boundary is the only thing identifying it as a control, so it owes
3:1. A divider or a card edge is decoration, and a hairline is the point. Reusing one token for
both would force every hairline in the product to the heavier value.

**A control's fill must be `--control-bg`, never `--surface-inset`.** The inset is translucent, and a
translucent fill on a `<select>` leaks: Chrome paints the option popup from the select's computed
background-color, so 3%-alpha white composites to an essentially white popup over a dark page. This
is not fixable with `color-scheme`, because an explicit background overrides the default rendering
that `color-scheme` selects. It is the reason the primitives exist: `Select` uses `--control-bg` and
never had the problem, while hand-rolled selects that reached for the nearest-looking surface token
all did.

| Token | Light | Dark |
| --- | --- | --- |
| `--control-bg` | `--surface-card` | `--surface-card` |
| `--control-border` | `#7d8b9e` | `#7a8290` |
| `--control-placeholder` | `#6b7280` | `#9ca3af` |
| `--control-disabled-bg` | `#f1f5f9` | `rgba(255, 255, 255, 0.04)` |
| `--control-disabled-border` | `#cbd5e1` | `#3f4653` |
| `--control-disabled-fg` | `#64748b` | `#8b93a1` |

### Rails

Navigation chrome has its own surface: a warm cream in light against the cool page tint, and a
charcoal one step above the page in dark, so a rail reads as chrome rather than as another card.

| Token | Light | Dark |
| --- | --- | --- |
| `--rail-bg` | `#f7f2e9` | `#242424` |
| `--rail-border` | `#e2d9c8` | `rgba(255, 255, 255, 0.12)` |
| `--rail-fg` | `#1f2937` | `#e5e7eb` |
| `--rail-fg-muted` | `#57534e` | `#a1a1aa` |
| `--rail-item-hover` | `#efe7d8` | `#2e2e2e` |
| `--rail-item-active` | `--action-tint` | `--action-tint` |
| `--rail-item-active-fg` | `--action-tint-fg` | `--action-tint-fg` |

### Status

Five tones, not five states. The recording states collapse onto them: queued, processing and note
generation are all `info`, distinguished by a spinner and a label rather than by colour; uploading
and paused are `warning`; processed is `success`; error and cancelled are `danger` and `neutral`.

Each tone has `-bg`, `-fg` and `-border`. Light keeps a tint background with dark text; dark
inverts to a translucent fill with light text. **The fill is not what distinguishes a tone.** A
50-level tint on a white card is about 1.05:1 by design, and the label and outline carry the
meaning, which is why the contrast audit does not measure it.

### Overlay and stacking

| Token | Light | Dark |
| --- | --- | --- |
| `--scrim` | `rgba(15, 23, 42, 0.55)` | `rgba(0, 0, 0, 0.66)` |
| `--float-shadow` | moderate, slate-tinted | moderate, black |
| `--tooltip-bg` / `--tooltip-fg` | `#1f2937` / `#f9fafb` | `#e5e7eb` / `#111827` |

Tooltips invert against the theme rather than sitting on the float surface, because a white tooltip
on a white card reads as part of the page.

Stacking order is fixed once, and everything that overlays something else takes its z-index from
here through a primitive: `--z-sticky` 30, `--z-dropdown` 40, `--z-modal` 50, `--z-toast` 60,
`--z-tour` 10001. The tour sits highest because driver.js draws its own overlay at 10000. Reach
these with `z-[var(--z-modal)]`, never with a bare number.

## Layout and density

Spacing, radii and control heights are tokens rather than per-component values, which is what makes
the density setting possible. `ViewportDensityProvider` sets `data-ui-density="compact"` on the
document element, and a single block in `tokens.css` re-declares the layout tokens at smaller
values. No component needs to know the density.

| Token group | Members |
| --- | --- |
| Root sizing | `--app-root-font-size` |
| Workspace | `--workspace-gap`, `--workspace-padding-x`, `--workspace-padding-x-md`, `--workspace-padding-y`, `--workspace-padding-y-md` |
| Workspace width | `--workspace-max-width-dense`, `--workspace-max-width`, `--workspace-max-width-wide`, `--workspace-max-width-feature` |
| Surfaces | `--surface-radius`, `--surface-radius-subtle`, `--surface-radius-panel`, `--surface-padding`, `--surface-padding-lg` |
| Controls | `--control-height-lg` |

Radii are one step tighter than the previous system: `--surface-radius` is 1.25rem, not 2rem. Cards
and controls stay at `rounded-lg`. Pills remain only for genuinely pill-shaped elements, meaning
tags, status badges and avatars.

Adding a hard-coded padding or radius to a component opts that component out of the density
setting. Use or extend the tokens instead. The radius tokens are also reachable as utilities:
`rounded-surface`, `rounded-surface-subtle`, `rounded-surface-panel`.

### Width is a property of the surface, not of the app

There is no single content width. A page of prose and a page of modules want opposite things from a
wide display, so each surface declares which cap it answers to: through a `workspace-shell-*` class
on `Workspace`, or, where a page predates that component, by reading the token directly, as the
people page does.

| Token | Comfortable | Compact | Used by |
| --- | --- | --- | --- |
| `--workspace-max-width-dense` | 120rem | 112rem | dashboard, people, live capture |
| `--workspace-max-width-wide` | 80rem | 74rem | tasks |
| `--workspace-max-width` | 72rem | 68rem | the `Workspace` default |
| `--workspace-max-width-feature` | 64rem | 60rem | the recordings landing panel |

A dense surface spends extra width on **more columns, not wider ones**. Nothing in the product grows
a text column past a comfortable reading measure, which is why widening the dashboard left every
prose surface beside it exactly where it was.

### The column model

Two surfaces use it: the dashboard and the live capture view. Both use three breakpoints, chosen so
that a column is never narrower than about 340px:

| Workspace width | Columns | Split |
| --- | --- | --- |
| below 54rem | 1 | |
| 54rem to 74rem | 2 | 1.1 / 1 |
| 74rem and above | 3 | 1.25 / 1 / 0.85 |

Columns are deliberately unequal, and they group by subject rather than by size. The calendar owns
the first, with the month grid and the agenda under it, because they are two views of one
subsystem; it is also the widest, since a day cell holds a number and up to four markers. The task
list has the second to itself, so it runs the full height of the page. Capture owns the third, with
what it produced under it: Meet Now, anything processing, then Recent Meetings. The list column
is the narrowest, because a row there is a title and a badge.

Each column pairs modules that keep their natural height with exactly one that absorbs the
remainder. Meet Now and the month grid stay the size they need; the agenda, the task list and
Recent Meetings take whatever is left.

The live capture view allocates by how much width a panel's content actually needs. Its capture
controls are a toolbar across the top rather than a card in a column, because a waveform and four
buttons do not need a column and the panels beside them do; the toolbar also carries the actions
that would otherwise be buried, which is why uploading a document lives there. Below it are two
columns, not three: every panel on this surface is dense prose, and a third column made all three
too narrow to read. The first carries the transcript with notes under it, and the pipeline's
progress in the transcript's place once recording stops, so pressing Stop reflows one column rather
than re-laying out the page. The second, slightly wider, is guidance. This is also why the view
moved off the 64rem feature cap: it is a console, not a page of prose, and a reading measure is
what forced five panels into one long scroll.

**A panel that subdivides must query itself, not the window.** Meeting Edge splits into two lists
side by side, and did so at the `xl` *viewport* breakpoint: at a 1280px window it subdivided a
400px column and wrapped both lists to three words a line. Any `grid-cols` inside a panel that can
land in a column belongs behind a container query on that panel. Settings now complies wholesale:
`SettingsBlock` and `SettingsCard` are containers, `SettingsRow` switches its label-beside-control
layout on its own width, and every grid under `components/settings/` queries its block or card.
The calendar provider credentials card is the cautionary tale: its two-provider grid engaged on
the `lg` viewport inside a 768px-capped column, leaving the label side of every row about 24px at
every window width — a layout that was unsatisfiable, not merely tight.

**These are container queries against the workspace, not media queries against the viewport**, and
that distinction is load-bearing. The nav rail is roughly 340px, resizable and collapsible, so the
space the grid actually has is the viewport minus a number the grid cannot see. A viewport
breakpoint gets it wrong in both directions: it withholds a column from someone who collapsed the
rail, and promises one to someone who widened it. Do not "simplify" these back to `lg`/`xl`/`2xl`.

**Never mix a `px` arbitrary breakpoint with the rem-based scale.** Tailwind orders breakpoint
variants by their declared value and cannot compare `1600px` against `80rem` without knowing the
root font size, so it emitted `@media (min-width:1600px)` *before* `@media (min-width:80rem)`. Both
match on a wide screen, specificity is equal, and the later rule wins, so an `xl:` two-column
template silently overrode a `min-[1600px]:` three-column one at every width. It compiles, it lints,
and it produces a layout that simply never appears. Keep every breakpoint in one unit.

Three rules follow from that grid:

- **Columns are `items-stretch`.** With `items-start` each column ended wherever its content did and
  the shorter one left an empty L-shape down the page. Stretching makes them end level, and a module
  that would overflow takes `flex-1 min-h-0 overflow-auto` and scrolls inside itself rather than
  pushing the grid taller. The page keeps its own scrollbar for the normal laptop case.
- **Every column needs one module that can absorb height.** A fixed-height module alone in a stretched
  column recreates the dead corner. Either pair it with a flexible one or let it grow, as the month
  grid does by sharing the leftover height between its week rows up to a cap.
- **A column that nothing fills does not exist.** The third column appears only when a module occupies
  it; when it is empty its occupants fold back into the second column, so the result is a two-column
  layout rather than an empty gutter.
- **The grid takes the height the window has left**, so the columns reach the bottom of the window
  instead of leaving a dead band under them. This is `grow` the whole way down from the workspace,
  *not* a percentage height: a percentage needs the parent's height to be definite, and this chain
  hands height down through `flex-grow` from a container that is `height: auto` with
  `min-height: 100%`. A percentage against that computes to zero and the declaration silently does
  nothing, which is a failure with no symptom other than the layout not happening. `grow` rather
  than `flex-1`, so a column taller than the window pushes past rather than being squeezed.
- **The fill is uncapped, deliberately.** Every way of capping it breaks something: a max-height on
  the grid leaves the box shorter than its own content once a column is genuinely long, and a
  max-height on the modules makes one column's cards end above the others, which is the dead corner
  this layout exists to remove. If a tall display ever makes an empty module look silly, cap that
  module.

Modules are direct children of the grid, not children of per-column wrappers. Wrappers still exist
for stacking, but they take `display: contents` at one column, which drops them out of the box tree so
that every module becomes a direct child of one flex column and `order-*` can sequence them freely.
A wrapper cannot reorder across its own boundary, so without this the phone order is forced to match
the desktop columns. On a phone the order is action first: capture, then anything processing, then
the agenda, tasks, Recent Meetings, and the month grid last. The desktop stack inside a column is
a separate sequence, set with a container-query `order` override where the two disagree, which is
what keeps a phone opening on the record button while a desktop leads its middle column with the
task list.

### Collapsible surfaces

Both rails and both meeting side panels collapse, and every collapse state persists in the
navigation store (`navigation-storage`). The pattern is uniform: the collapse control lives in the
surface's own header, a slim strip carries the re-open affordance when nothing else is left, and
nothing auto-collapses — the user decides, the choice survives a reload, and mobile keeps its own
layouts (drawer, full-width list, tabs) untouched. The meeting view's Speakers and Chat panels
collapse independently; collapsing one gives the other the full column height, and collapsing both
reduces the right column to an icon strip. The recordings rail collapses to the same strip width
as the nav rail so the two align.

### Surface nesting

The flat canon's two-level rule, as a fix pattern. When a surface turns out to be nested, it steps
down rather than repeating:

| Depth | Wrong | Right |
| --- | --- | --- |
| Outer | `bg-surface-card` + border + `shadow-card` | unchanged |
| Second | `bg-surface-card` + border + `shadow-card` | `bg-surface-inset`, no border, no shadow |
| Third | `bg-surface-card` + border + `shadow-card` | no surface at all: spacing and a divider |

The test for a surface is a *container* with a solid card fill and a hairline border. A button, a
pill, an input or a calendar cell that uses the card fill as a control background is not a level in
the stack, and holding controls to this rule would leave them with nothing to sit on. The judgement
is whether the element is a region of the page or something you click.

### Modules

A dashboard module is a presentational component with three properties:

- **It does not render its own empty state.** The parent decides whether there is anything to show
  and omits the module entirely if there is not, so a quiet account sees a smaller dashboard rather
  than a wall of empty boxes.
- **There is a floor.** The calendar, the agenda, capture and the task list always render, so the
  dashboard is never blank.
- **Data comes from the parent.** Where two modules are two views of one subsystem, the parent calls
  the hook once and passes the value to both. A hook that fetches must be called once per subsystem,
  not once per module, or the request doubles silently.

## Primitives

Shared components live in [frontend/src/components/ui/](../frontend/src/components/ui/) and consume
tokens exclusively. Build a surface out of these rather than out of raw elements.

| Primitive | Use for |
| --- | --- |
| `Button` | Any labelled action. `variant`: primary, secondary, ghost, danger. `size`: sm, md, lg. Handles `loading` by disabling itself and swapping the leading icon for a spinner, keeping the label so the control does not resize. |
| `IconButton` | An action with no label. `aria-label` is required, because it is the only name the control has. The smallest size renders a 16px glyph inside a 40px box, so the target stays reachable. |
| `Card` | The canonical resting surface. `interactive` switches on the tint-and-border hover treatment. |
| `Badge`, `StatusBadge` | Pills. `StatusBadge` maps a `RecordingStatus` to a tone, a glyph and a label. |
| `Input`, `Select` | Form fields. Both draw on the shared `fieldChrome` helper, so they cannot drift apart. `Select` is a native select, because the mobile pass wants the platform picker on a phone; see `color-scheme` below for what that costs. |
| `Modal` | Every dialog. Wraps the Headless UI dialog, so the focus trap, scroll lock, Escape handling and portal come from a maintained implementation. Adds the plain scrim, the float surface and shadow, a token z-index, and the height cap and viewport gutter that stop a tall modal pushing its own actions below the fold on a phone. |
| `Switch`, `Tooltip`, `ModernDatePicker`, `MultiSelect` | Pre-existing widgets, restyled onto tokens. |
| `FitText` | A single line that must fit its container, meaning the meeting title. Steps the font from a designed size down to a readability floor, then wraps to two clamped lines. CSS cannot do this: `clamp()` tracks the container's size, never the text's own length. |

## Typography

`Geist` and `Geist_Mono` are loaded in
[frontend/src/app/layout.tsx](../frontend/src/app/layout.tsx) and exposed as `--font-geist-sans` and
`--font-geist-mono`, which `@theme inline` maps to Tailwind's `--font-sans` and `--font-mono`.

Geist Sans is the body font, set once on `body` in `globals.css`, so it is inherited everywhere
rather than applied per component. Geist Mono is applied where it is wanted, through `font-mono`.

`--font-geist-sans` already resolves to `Geist, Geist Fallback`, where the fallback is Arial
carrying `size-adjust` and ascent/descent overrides that Next generates to match Geist's metrics.
That is what covers the window before the webfont resolves, and it is why the swap does not visibly
reflow the page. The generic families after the variable are a last resort for the case where the
font class is absent entirely.

There is no need to add a `font-sans` class to reach the body font; an element only needs one when
it is overriding something else back to the default.

### The heading scale

`density-heading-page` and `density-heading-section` carry the fluid heading sizes: a viewport
clamp below the desktop breakpoint, and a second clamp under compact desktop density. Their
font-size declarations live *outside* the cascade layers on purpose. Inside `@layer components`
they lose to the `text-*` utilities on the same elements, because the utilities layer is declared
after components and layer order beats selector specificity — which is exactly how the compact
scaling originally shipped inert. Keep them unlayered, and keep each clamp's upper bound at the
largest `text-*` size the class is paired with, so a clamp only ever steps a heading down.

Where a heading must respond to its own content rather than to its container — the meeting title
is the case — CSS cannot help, and the `FitText` primitive measures and fits instead.

Lucide is the sole icon system.

## Accessibility

The contrast target is strict WCAG 2.2 AA, and it is enforced rather than reviewed.
[frontend/scripts/check-contrast.mjs](../frontend/scripts/check-contrast.mjs) parses the token
blocks out of `tokens.css` and out of the marketing site's `site-tokens.css`, resolves `var()`
indirection, composites translucent values over their backdrop stack, and measures a declared list
of pairings across four themes: the app's light and dark, and the site's light and dark, which are
the app themes overlaid with the site-only families. It runs as `npm run check:contrast` in the
`Frontend lint` CI job and dependency-free as `node frontend/scripts/check-contrast.mjs` in the
`Site build` job.

Contrast is a property of a pair, not of a colour, and that relationship lives nowhere in CSS.
Declaring the pairs in the script is what makes them reviewable. **A pairing that cannot be
expressed in tokens is a smell in the tokens.** Fix the tokens rather than widening the list with
literals.

The thresholds it applies:

- **4.5:1** for text, including button labels and placeholder text.
- **3:1** for large text, and for the visual information required to identify a component or its
  state. That covers a control's boundary and a focus ring. It does *not* cover a card edge, a
  divider, or the outline of a pill whose fill and label already carry the meaning; holding those
  to 3:1 would replace the hairlines this design is built on with wireframe outlines.
- **1.15:1** for decorative separators. This is a house floor rather than a WCAG requirement: a
  hairline is exempt from the standard but still has to be visible, so a token that has drifted to
  invisible fails here rather than passing by exemption.

Beyond contrast, conformance is a matter of review. The project runs no `axe`, `jsx-a11y` or
Playwright gate, so keyboard reachability and screen-reader behaviour are verified by hand when
changing the interface. The primitives carry what can be carried structurally: required labels on
`IconButton`, `aria-invalid` and `aria-describedby` wiring in `Input` and `Select`, `aria-busy` on
a loading button, and a real focus trap in `Modal`.

## The marketing site

`www.nojoin.co.uk` lives in `site/` as an Astro build deployed to Cloudflare Workers static
assets. It runs this system at marketing scale: `Base.astro` imports
`frontend/src/app/tokens.css` by relative path, and `site/plugins/tokens-theme.mjs` maps the
app's class-based dark theme onto the site's own theming, so the site and the app cannot
disagree about what a token means.

**The site has two theme inputs, and a token family is only correct if it answers to both.**
One is the OS `prefers-color-scheme`; the other is an explicit choice the header toggle
stores and `Base.astro` stamps as `data-theme` before the first paint. So every dark block
ships as a pair of rules — `:root:not([data-theme="light"])` inside the media query, and
`:root[data-theme="dark"]` outside it — both generated by the plugin from one set of
declarations. The combinations that matter are the two where the inputs **disagree**, and
checking only "light" and "dark" will not find a bug in either of them.

`site/src/styles/site-tokens.css` holds the families the app has no equivalent of: syntax
highlighting for the quick-start block, the screenshot frame's chrome, the closer band's
inverse and ghost buttons, the marketing type scale, and the three marketing effects below.
Those are tokens rather than literals precisely so the contrast audit can measure them.

**The code block is dark in both themes**, on its own `--code-bg` rather than the shared
`--surface-inset`, and it is the one surface on the site that does not follow the theme. It
used to: the text was legible either way, but composited over the page the block came out at
1.04:1 in light and 1.07:1 in dark, so it read as a faint rectangle rather than a panel. That
is a separation problem, not a legibility one, and the contrast gate is silent on it because
AA has nothing to say about a decorative fill against the page behind it. Following the theme
could not have fixed dark in any case — the page is already near-black, so the furthest a
darker fill gets is about 1.1:1. A dark block reaches 16.96:1 against the light page, keeps
the worst syntax pairing at 6.92:1, and is what a reader expects code to look like. One
syntax palette now serves both themes.

The site takes three deliberate departures from the flat canon, all site-only:

- A halo and a 1px inset white top edge under the primary button.
- One soft radial wash behind the hero and the agent band — the only gradient in either
  surface, and the reason the flat canon's "no gradients" line is scoped to the app.
- An orange edge on every screenshot frame, plus a softer, wider lift on floating cards than
  the app's resting shadow.

The app stays flat because a lifted control is noise in a workspace someone stares at all
day; the site is a page whose job is to be looked at once. All three are decoration over
fills that already answer to the gate, so none can hide a failing pair.

The contrast audit covers the site as two extra themes, `site-light` and `site-dark`, driven
by `SITE_PAIRINGS` in `frontend/scripts/check-contrast.mjs`. It has two blind spots, and the
second is wider than the first:

- **It only checks pairings it has been told about**, so it passes silently on any
  combination nobody registered. Adding the pairing is part of adding the furniture.
- **It measures values, never whether the block holding them applies.** It parses each theme
  into a flat set of tokens and compares the colours; it has no model of selector resolution.
  `site-tokens.css` once had no `data-theme` handling at all, so a visitor who chose light on
  a machine set to dark got the dark syntax colours on a light surface — grey on near-white,
  in the quick-start block. Both site themes passed throughout, because both sets of values
  were individually correct. A green gate means the colours are legible where they apply; it
  is not evidence that a visitor can reach them.

Reachability is checked by the build (`tokens-theme.mjs` throws rather than silently skipping
a file it cannot parse, and fails the build if `site.css` hides `site-tokens.css` behind an
`@import` again) and in a browser, across the four-way theme matrix described in
[SITE.md](SITE.md).

[SITE.md](SITE.md) is the authority on the site's composition, voice and claims, and carries
the preview runbook. This document remains the authority on tokens.

## Related documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): where the web client sits in the system.
- [DEVELOPMENT.md](DEVELOPMENT.md): running and building the frontend.
- [USAGE.md](USAGE.md): the interface as a user encounters it.

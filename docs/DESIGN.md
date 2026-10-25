# Design System

The web client's tokens are CSS custom properties in
[frontend/src/app/tokens.css](../frontend/src/app/tokens.css), with optional palettes in
[palettes.css](../frontend/src/app/palettes.css). `globals.css` imports both, maps them to Tailwind
utilities in `@theme inline`, and holds the app-only furniture. Components reach the tokens through
those utilities and the [primitives](#primitives). The marketing site uses the same tokens; see
[The marketing site](#the-marketing-site).

## Default: the flat canon

Every resting surface is a solid fill with a 1px hairline border. There are no gradients, no
translucent glass and no `backdrop-filter`. The brand gradient appears only in the logo.

1. **Two surface levels.** The page, and one card on it. `--surface-inset` is for composite UI
   inside a card, such as meters and consoles. No card contains another card; see
   [Surface nesting](#surface-nesting).
2. **Light separates by shadow, dark by lightness.** `--surface-card-shadow` is a 4% shadow in
   light mode and a transparent zero shadow (`0 0 #0000`) in dark, which draws nothing.
3. **Floats are the exception.** Modals, dropdowns, popovers, tooltips and toasts use
   `--surface-float` and carry `--float-shadow` in both themes. In dark mode the float surface is
   lighter than a card, since a scrim cannot darken a page that is already dark. A float does not
   count as a third surface level.
4. **Affordance is colour.** A clickable card tints its background and strengthens its border on
   hover. Nothing lifts, and no hover state adds a shadow.
5. **Scrims are a plain dim** (`--scrim`, no blur). Sticky chrome is fully solid.
6. **Focus is a 2px `--focus-ring` outline** on every interactive element.
7. **Motion is quick and flat**: short transitions (0.15 to 0.3s), no bounce, reduced motion
   respected, and no transition that only animates a shadow.

## Themes

Light and dark are both first-class. Dark mode is class-based, so a user can override the
operating system preference:

```css
@custom-variant dark (&:where(.dark, .dark *));
```

Raw values sit on `:root` and are overridden on `.dark`, which declares only what changes.
`@theme inline` references them through `var()`, so every generated utility follows the theme.

**A component must not branch on the theme.** A `dark:` variant carrying a colour means a token
is missing. `dark:` stays legitimate for non-colour concerns, such as swapping an image, and for
`dark:prose-invert` on typography-plugin content.

**`color-scheme` is required on both themes** (`light` on `:root`, `dark` on `.dark`). The
browser draws native controls (a `<select>` popup, checkboxes, radios, range thumbs, date
pickers) and no selector reaches inside them, so without `color-scheme` they render light in
dark mode.

**A vendor's colours are overridden through the vendor's own selectors.** react-datepicker
colours its time column and popover arrow through long selectors, and a shorter override loses
to them, which left the time list white in dark mode. `globals.css` repeats the library's
selectors for those parts with tokens, so they win on order without `!important`, and
`datepickerTheme.test.ts` fails if the library colours a part that is not repeated.

### Appearance preferences

Theme, colour palette, corner style and density are per-browser preferences in
**Settings > Appearance**. `lib/appearance.ts` owns their allowed values and local storage keys.
`lib/theme-script.ts` applies the stored choice to `<html>` before first paint, and
`ThemeProvider` and `ViewportDensityProvider` keep it in sync afterwards. The script ships as an
uncompiled string, so it must stay plain JavaScript; `theme-script.test.ts` executes it.

| Preference | On `<html>` | Default |
| --- | --- | --- |
| Theme | `.dark` class | follows the system |
| Palette | `data-palette` = `graphite`, `classic`, `ultraviolet` or `marigold` | no attribute (the `:root` / `.dark` tokens) |
| Corner style | `data-corners` = `subtle` or `square` | no attribute (rounded) |
| Density | `data-ui-density` = `comfortable`, `compact` or `dense` | resolved from the viewport (never `dense`) |
| Style seam | `data-style` = `default` | always Default; no settings control yet |

A new preference must keep the same property: its default leaves the product exactly as it looks
without the preference.

The style vocabulary currently contains only `default`. `nojoin-style` is validated by the
same storage helpers as the other preferences; Default removes the key. The pre-paint script
and runtime storage listener always apply `data-style="default"`, including with invalid or
blocked storage. This reserves a separate appearance axis without changing the flat canon.

### Palettes

A palette is a pair of blocks in `palettes.css`: `html[data-palette="x"]:not(.dark)` and
`html[data-palette="x"].dark`. Keep the `:not(.dark)`. `html[data-palette]` alone outranks
`.dark`, so an unscoped light block leaks into dark mode for any token the dark block does not
repeat.

A palette may re-tint the neutrals (page, card, inset, float, rails, text greys, hairlines,
control borders) and replace the action family (fill, label, text, tint, border, focus ring). It
must not touch status tones, danger, chart series or speaker colours, which carry meaning and
look the same in every palette. Its page must therefore stay light enough for the shared
`--danger-text` to clear 4.5:1: Classic keeps its Windows grey (`#d4d0c8`) for the rails and uses
`#ece9d8` for the page.

`check-contrast.mjs` discovers palettes in the file and audits both blocks against the full app
pairing list.

A dark-mode fill has to clear 3:1 against the card and still carry its label at 4.5:1. Classic and
Marigold manage that by inverting the dark fill to a light colour with a dark `--action-on`.
**Always label an action fill with `text-action-on` or `text-action-on-muted`**, never with white
or another text colour; `actionFillUsage.test.ts` checks this.

## Colour

### Action, the brand

| Token | Light | Dark | Purpose |
| --- | --- | --- | --- |
| `--action` | orange-700 `#c2410c` | orange-700 `#c2410c` | Primary fills |
| `--action-hover` | orange-800 `#9a3412` | orange-800 `#9a3412` | |
| `--action-active` | orange-900 `#7c2d12` | orange-900 `#7c2d12` | |
| `--action-on` | `#ffffff` | `#ffffff` | Label on an action fill |
| `--action-on-muted` | orange-100 `#ffedd5` | orange-100 `#ffedd5` | Secondary text on an action fill |
| `--action-text` | orange-700 `#c2410c` | orange-400 `#fb923c` | Links, accents, active text |
| `--action-text-hover` | orange-800 `#9a3412` | orange-300 `#fdba74` | |
| `--action-tint` | orange-100 `#ffedd5` | orange-500 at 18% | Selected or active row |
| `--action-tint-hover` | orange-200 `#fed7aa` | orange-500 at 26% | |
| `--action-tint-fg` | orange-800 `#9a3412` | orange-200 `#fed7aa` | Text on the tint |
| `--action-border` | orange-300 `#fdba74` | orange-400 at 35% | Tint outlines |
| `--focus-ring` | orange-600 `#ea580c` | orange-400 `#fb923c` | Focus outline only |

**The fill is orange-700.** White on orange-600 is 3.56:1, below the 4.5:1 required of all text,
button labels included.

**Use `--action` for something filled and `--action-text` for something written.** They coincide
in light mode. In dark mode orange-700 carries a white label at 5.18:1 but reads at only 3.49:1 as
text on the page, so `--action-text` lifts to orange-400.

`--focus-ring` may stay lighter because it is never text: it answers to the 3:1 non-text
threshold.

### Danger

Shaped like the action family. The fill is red-600 in both themes (a white label at 4.83:1); only
the text form changes.

| Token | Light | Dark |
| --- | --- | --- |
| `--danger` | red-600 `#dc2626` | red-600 `#dc2626` |
| `--danger-hover` | red-700 `#b91c1c` | red-700 `#b91c1c` |
| `--danger-active` | red-800 `#991b1b` | red-800 `#991b1b` |
| `--danger-on` | `#ffffff` | `#ffffff` |
| `--danger-text` | red-700 `#b91c1c` | red-400 `#f87171` |
| `--danger-text-hover` | red-800 `#991b1b` | red-300 `#fca5a5` |

### Contrast tokens

Use these instead of raw Tailwind greys for body-adjacent text and borders.

| Token | Light | Dark |
| --- | --- | --- |
| `--foreground` | `#171717` | `#ededed` |
| `--contrast-muted` | `#374151` | `#e5e7eb` |
| `--contrast-helper` | `#4b5563` | `#d1d5db` |
| `--contrast-icon-muted` | `#52525b` | `#a1a1aa` |
| `--contrast-border` | `#cbd5e1` | `#4b5563` |
| `--contrast-border-strong` | `#94a3b8` | `#6b7280` |

### Surfaces

| Token | Light | Dark |
| --- | --- | --- |
| `--surface-page` | `#f8fafc` | `#161616` |
| `--surface-card` | `#ffffff` | `#202020` |
| `--surface-card-border` | `rgba(203, 213, 225, 0.58)` | `rgba(75, 85, 99, 0.38)` |
| `--surface-divider` | `rgba(203, 213, 225, 0.42)` | `rgba(75, 85, 99, 0.25)` |
| `--surface-card-shadow` | `0 1px 2px 0 rgb(15 23 42 / 0.04)` | `0 0 #0000` |
| `--surface-inset` | `rgba(241, 245, 249, 0.75)` | `rgba(255, 255, 255, 0.03)` |
| `--surface-float` | `#ffffff` | `#2a2a2a` |
| `--surface-float-border` | `rgba(203, 213, 225, 0.9)` | `rgba(148, 163, 184, 0.22)` |

The hairlines sit close to the 1.15:1 house floor in both themes on purpose; in light mode the
shadow, not the border, lifts a card. Against the dark card `--action` clears the 3:1 a resting
fill owes by only 3.15:1, so the dark page, card and float move together: lightening the card
alone fails the audit.

**A shadow token is never `none`**, in any theme or palette. Tailwind writes `shadow-card` and
`ring-*` into one `box-shadow` list, and `none` inside a list makes the whole declaration
invalid, so an element with both would draw no ring. Write "no shadow" as `0 0 #0000`, which
composes and draws nothing; `shadowTokens.test.ts` fails on a `none`.

### Controls

| Token | Light | Dark |
| --- | --- | --- |
| `--control-bg` | `--surface-card` | `--surface-card` |
| `--control-border` | `#7d8b9e` | `#7a8290` |
| `--control-placeholder` | `#6b7280` | `#9ca3af` |
| `--control-disabled-bg` | `#f1f5f9` | `rgba(255, 255, 255, 0.04)` |
| `--control-disabled-border` | `#cbd5e1` | `#3f4653` |
| `--control-disabled-fg` | `#64748b` | `#8b93a1` |

`--control-border` owes 3:1 because a control's boundary is what identifies it. Dividers and card
edges are decoration and stay hairlines, so keep `--control-border` and `--contrast-border`
separate.

**A control's fill must be `--control-bg`, never `--surface-inset`.** The inset is translucent,
and Chrome paints a `<select>`'s option popup from its computed background colour, so a
translucent fill gives a near-white popup over a dark page. `color-scheme` cannot fix this,
because an explicit background overrides it. `Select` and the settings control classes already
use `--control-bg`.

### Rails

Navigation chrome has its own surface: a warm cream in light, and a charcoal one step above the
page in dark.

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

Five tones, `neutral`, `info`, `success`, `warning` and `danger`, each with `-bg`, `-fg` and
`-border` tokens. `StatusBadge` maps recording states onto them: queued, processing and note
generation are `info`, told apart by a spinner and a label; uploading and paused are `warning`;
processed is `success`; error is `danger`; recorded and cancelled are `neutral`.

Light uses a tint with dark text, and dark a translucent fill with light text. The label and
outline carry the tone, so the fill is not measured (a 50-level tint on a white card is about
1.05:1). The dark fills only read correctly on an opaque surface: `NotificationToast` paints
`--surface-float` first and lays the tone's `-bg` over it.

### Chart and speaker colours

`--chart-1` to `--chart-8`, `--chart-other` and `--chart-grid` have separate values per theme.
The seventeen `--speaker-*` tokens, one per speaker palette key, are the same in both themes.
[DEVELOPMENT.md](DEVELOPMENT.md#charts) has the rules for using them.

### Overlay and stacking

| Token | Light | Dark |
| --- | --- | --- |
| `--scrim` | `rgba(15, 23, 42, 0.55)` | `rgba(0, 0, 0, 0.66)` |
| `--float-shadow` | moderate, slate-tinted | moderate, black |
| `--tooltip-bg` / `--tooltip-fg` | `#1f2937` / `#f9fafb` | `#e5e7eb` / `#111827` |

Tooltips invert against the theme, so a tooltip never looks like part of a white card.

Anything that overlays other content takes its z-index from a token, through a primitive or as
`z-[var(--z-modal)]`, never as a bare number:

| Token | Value | For |
| --- | --- | --- |
| `--z-sticky` | 30 | sticky chrome |
| `--z-dropdown` | 40 | dropdowns, menus, tooltips |
| `--z-modal` | 50 | dialogs |
| `--z-popover` | 55 | a context menu or popover that can open from inside a dialog |
| `--z-toast` | 60 | toasts |
| `--z-tour` | 10001 | the guided tour, above driver.js's own overlay at 10000 |

## Layout and density

Spacing, radii, widths and control heights are tokens. `ViewportDensityProvider` sets `data-ui-density` on `<html>`, and blocks in `tokens.css`
re-declare the layout tokens for each level, so no component needs to know the density.
`resolveDensity()` in `lib/viewportDensity.ts` makes the decision and the pre-paint script mirrors
it: the user's explicit choice, or under **Automatic (by window size)**, Compact for a window at
least 1024px wide and no larger than 1920 × 1080, and Comfortable otherwise.

A hard-coded padding or radius opts a component out of density. Use or extend the tokens. The
radius tokens are also utilities: `rounded-surface`, `rounded-surface-subtle` and
`rounded-surface-panel`.

| Token group | Members |
| --- | --- |
| Root sizing | `--app-root-font-size` |
| Workspace | `--workspace-gap`, `--workspace-padding-x`, `--workspace-padding-x-md`, `--workspace-padding-y`, `--workspace-padding-y-md` |
| Workspace width | `--workspace-max-width-dense`, `--workspace-max-width`, `--workspace-max-width-wide`, `--workspace-max-width-feature` |
| Surfaces | `--surface-radius`, `--surface-radius-subtle`, `--surface-radius-panel`, `--surface-padding`, `--surface-padding-lg` |
| Controls | `--control-height-lg` |
| Line height | `--leading-relaxed-step` (Dense only) |

`useViewportDensity().isCompact` is true for compact and dense alike, so layout code that narrows
a rail for compact narrows it for dense too.

### Dense

Dense is opt-in only: Automatic never chooses it. Its block starts from compact's values and
tightens them, split by input. Keep that split when tuning it.

- **Everywhere:** spacing that is never a touch target (workspace gaps and padding, card padding,
  surface radii), wider workspace caps to offset the smaller root size, and
  `--leading-relaxed-step`, which Tailwind's `leading-relaxed` reads (1.625 by default, 1.45 in
  Dense).
- **`@media (pointer: fine)` only:** the 14px root font size that every rem padding and height
  hangs off, and `--control-height-lg` (2.5rem). On a coarse pointer Dense keeps compact's 15px
  root and 3.25rem controls, so it never makes a touch target smaller than Compact does.

### Corner radii

Every radius is a multiple of `--radius-scale`: 1 by default, 0.5 under
`html[data-corners="subtle"]` and 0 under `html[data-corners="square"]`. `globals.css`
re-declares Tailwind's `rounded` and `rounded-xs` through `rounded-4xl` at their stock sizes times
the scale, so `rounded-lg` is still 8px by default and follows the Corner style setting.

- **Text-bearing pills** (a tag, a status badge, a count) use `rounded-pill`, backed by
  `--pill-radius`. A pill is half its height, not a length, so it cannot be scaled; Subtle sets
  `--pill-radius` to 0.375rem and Square to 0.
- **`rounded-full` is for true circles** (avatars, dots, round icon buttons, spinners), which
  stay circles in every corner style.
- **An arbitrary radius goes through the scale**, as in `rounded-[calc(5px*var(--radius-scale))]`.
  A bare `rounded-[5px]` ignores the setting.

`radiusUsage.test.ts` fails on `rounded-full` with horizontal padding in one class string, and on
an arbitrary radius without the scale.

Third-party corners follow the scale too: the typography plugin's `<pre>` and `<kbd>` (in
`globals.css`) and every fixed radius in react-datepicker's stylesheet (`datepicker-radius.css`,
checked against the library by `datepickerRadius.test.ts`).

**Deliberate exceptions.** These stay round under Subtle and Square, because their shape is what
they are:

- circles: avatars, colour and status dots, round icon buttons (the chat button, calendar month
  arrows), spinners, the selected day in the task deadline calendar, react-datepicker's clear
  button;
- the `Switch` track and thumb;
- progress and level bars (uploads, document parsing, model downloads, the capture level meters);
- scrollbar thumbs.

### Width is a property of the surface, not of the app

There is no single content width. Each surface declares its cap through a `workspace-shell-*`
class on `Workspace` or, as the people page does, by reading the token directly.

| Token | Comfortable | Compact | Dense | Used by |
| --- | --- | --- | --- | --- |
| `--workspace-max-width-dense` | 120rem | 112rem | 128rem | dashboard, people, live capture |
| `--workspace-max-width-wide` | 80rem | 74rem | 84rem | tasks |
| `--workspace-max-width` | 72rem | 68rem | 76rem | the `Workspace` default |
| `--workspace-max-width-feature` | 64rem | 60rem | 68rem | the recordings landing panel |

A wide surface spends extra width on **more columns, not wider ones**. No text column grows past a
comfortable reading measure.

### The column model

The dashboard and the live capture view lay modules out in columns. The dashboard uses three
breakpoints, chosen so a column is never narrower than about 340px:

| Workspace width | Columns | Split |
| --- | --- | --- |
| below 54rem | 1 | |
| 54rem to 74rem | 2 | 1.1 / 1 |
| 74rem and above | 3 | 1.25 / 1 / 0.85 |

Its columns group by subject. The calendar takes the widest first column, with the month grid
and the agenda under it. The task list has the second to itself. Capture takes the narrowest
third, with what it produced under it: Meet Now, anything processing, then Recent Meetings.

The rules:

- **Query the workspace, not the viewport.** The nav rail is resizable and collapsible, so the
  viewport does not tell the grid how wide it is. Do not "simplify" these container queries back
  to `lg`/`xl`/`2xl`.
- **A panel that subdivides queries itself.** Any `grid-cols` inside a panel that can land in a
  column belongs behind a container query on that panel. Settings does this throughout:
  `SettingsCard` and `SettingsBlock` are containers, `SettingsRow` switches layout on its own
  width, and every grid under `components/settings/` queries its block or card. The meeting
  view's Analytics tab queries its own scroll region, because it sits in a resizable panel
  between two collapsible rails.
- **Never mix a `px` arbitrary breakpoint with the rem-based scale.** Tailwind cannot order
  `1600px` against `80rem`, so the rule it emits later can win at every width. Keep every
  breakpoint in one unit.
- **Columns are `items-stretch`**, so they end level. A module that would overflow takes
  `flex-1 min-h-0 overflow-auto` and scrolls inside itself instead of pushing the grid taller.
- **Every column has exactly one module that absorbs the leftover height.** The others keep their
  natural height: Meet Now and the month grid stay the size they need, while the agenda, the
  task list and Recent Meetings take what is left.
- **Do not cap the fill.** A `max-height` on the grid leaves it shorter than its own content once
  a column is long, and one on a module makes that column's cards end above the others. If a tall
  display ever makes an empty module look odd, cap that module.
- **The grid takes the height the window has left**, through `grow` the whole way down from the
  workspace. A percentage height computes to zero in this chain. Use `grow` and not `flex-1`, so a
  column taller than the window pushes past instead of being squeezed.
- **A column that nothing fills does not exist.** The third column appears only when Recent
  Meetings or a processing card has something to show; otherwise capture folds back into the
  second column.
- **Modules are direct children of the grid.** Column wrappers take `display: contents` at one
  column, so every module joins one flex column and `order-*` can sequence them freely. The phone
  order is action first: Meet Now, anything processing, the agenda, tasks, Recent Meetings, and
  the month grid last. The desktop order inside a column is set separately, with a
  container-query `order` override.

**The live capture view** has a toolbar across the top (recording state, meeting name, waveform,
controls, document upload) and, from 54rem, two columns. The first holds the transcript, replaced
by a progress card once recording stops, with notes and documents under it. The second, slightly
wider, holds Meeting Edge guidance, which absorbs height and scrolls inside its cell. In the first
column the transcript absorbs while recording and the notes editor afterwards.

This view hands down a definite height instead of a floor: `Workspace` gets `h-full` (the
dashboard has `min-h-full`), with `min-h-0` down the chain and `grid-template-rows: minmax(0, 1fr)`
on the grid. Without all three the tallest column sizes the row and no panel can scroll. **A
child of an absorbing module takes the height its card was given**: from 54rem the transcript
window is `min-h-0` and the card clips what it cannot hold. Its 14rem floor applies only in the
stacked layout below 54rem, where the page scrolls.

**Tables in narrow containers** use `.analytics-table` or `.settings-table` from `globals.css`.
Below 34rem of container width the header row goes and each row becomes a list labelled from its
cells' `data-label` attributes, with one DOM at every width. A cell without a `data-label`, such
as the row's actions, spans the row.

### Collapsible surfaces

Both rails and both meeting side panels collapse, and each collapse state persists in the
navigation store (`navigation-storage`). The collapse control sits in the surface's own header, a
slim strip carries the re-open control, nothing auto-collapses, and mobile keeps its own layouts
(drawer, full-width list, tabs). The meeting view's Speakers and Chat panels collapse
independently: collapsing one gives the other the full column height, and collapsing both
reduces the right column to an icon strip. The recordings rail collapses to the nav rail's strip
width so the two align.

### Surface nesting

The flat canon's two-level rule, as a fix pattern. A nested surface steps down:

| Depth | Wrong | Right |
| --- | --- | --- |
| Outer | `bg-surface-card` + border + `shadow-card` | unchanged |
| Second | `bg-surface-card` + border + `shadow-card` | `bg-surface-inset`, no border, no shadow |
| Third | `bg-surface-card` + border + `shadow-card` | no surface at all: spacing and a divider |

A surface is a region of the page with a solid card fill and a hairline border. A button, pill,
input or calendar cell that uses the card fill as a control background does not count as a
level.

### Modules

A dashboard module is a presentational component:

- **It does not render its own empty state.** The parent omits it when there is nothing to show.
- **There is a floor.** The calendar, the agenda, Meet Now and the task list always render, so
  the dashboard is never blank.
- **Data comes from the parent.** Where two modules are two views of one subsystem, the parent
  calls the hook once and passes the result to both, so the request is not made twice.

## Primitives

Shared components in [frontend/src/components/ui/](../frontend/src/components/ui/) consume tokens
only. Build a surface from these before reaching for raw elements.

| Primitive | Use for |
| --- | --- |
| `Button` | Any labelled action. `variant`: primary, secondary, ghost, danger. `size`: sm, md, lg. `loading` swaps the leading icon for a spinner, keeping the label so the control does not resize, and ignores clicks, a submit button's included. The button stays enabled and marked `aria-disabled`, even when `disabled` is also set, because Chrome moves focus from a disabled button to the page body. |
| `IconButton` | An action with no label. `aria-label` is required, as the control's only name. The smallest size draws a 16px glyph in a 40px box, so the target stays reachable. `loading` works as on `Button`. |
| `touchTarget.ts` | Gives a control at `sm` the full 44 × 44px target on a touch screen: `TOUCH_TARGET_SQUARE` for an `IconButton`, `TOUCH_TARGET_HEIGHT` for a `Button`. The sizes are px, so the smaller root of compact and dense cannot shrink them. |
| `Card` | The resting surface. `interactive` turns on the tint-and-border hover. |
| `Badge`, `StatusBadge` | Pills. `StatusBadge` maps a `RecordingStatus` to a tone, a glyph and a label. |
| `Input`, `Select` | Form fields, both built on the shared `fieldChrome` helper. `Select` is a native select, for the platform picker on a phone; it relies on `color-scheme` (see [Themes](#themes)). |
| `Modal` | Every dialog. Wraps the Headless UI dialog for the focus trap, scroll lock, Escape handling and portal, and adds the plain scrim, the float surface and shadow, `--z-modal`, and a height cap so a tall modal keeps its actions on screen on a phone. |
| `Switch`, `Tooltip`, `ModernDatePicker`, `MultiSelect` | Widgets on the same tokens. |
| `useAnchoredPanel` | Positions a dropdown or popover against its trigger with `position: fixed`, flipping above when there is more room and clamping to the viewport, so a panel opened inside a modal is not clipped. |
| `FitText` | A single line that must fit its container, such as the meeting title. Steps the font down from a designed size to a readability floor, then wraps to two clamped lines. |

### Native bases and style hooks

`nativeBases.tsx` supplies `ButtonBase`, `InputBase`, `SelectBase` and `TextareaBase` for controls
that already have their own token-backed recipes. The styled primitives use these same hosts.
The bases forward native props and refs verbatim, add no wrappers, and do not merge classes,
invent ids or supply a button type. An omitted type keeps the browser's submit behaviour.
Keep the existing styled primitives for new standard controls; use a base where an existing
recipe or layout needs its native element unchanged.

`SurfaceBase` preserves the original semantic tag through `as` and distinguishes `card`,
`inset`, `float` and `pane` surfaces through `kind`. It adds no padding, border or shadow.
`Card` and settings surfaces retain their existing token-backed recipes on this shared seam.
A control, divider, avatar or placeholder using a card background is not itself a card.
Linked recording cards put the same surface markers directly on Next's `Link`, retaining
its navigation, prefetching and anchor behaviour.

The `data-ui` markers are inert under Default. Buttons distinguish `data-ui-chrome="action"`
from `plain` rows, tabs and text controls. Text and date/time inputs use `data-ui-input="field"`;
checkbox, radio, range, file, color, hidden and button-like inputs use `data-ui-input="native"`.
Surfaces expose `data-ui-surface`; Modal and custom widgets mark their existing host nodes
without replacing Headless UI or vendor DOM. Future style CSS can target these markers under
an explicit `html[data-style="…"]` selector while continuing to consume palette, radius,
density and shadow tokens. Do not style all buttons indiscriminately, change native controls
into text fields, or use a shadow declaration that erases selection rings. Default must remain
pixel-identical to an unset style attribute.

**A raw button busy with its own request behaves as a loading `Button` does.** Where a control
cannot be a `Button`, such as a settings button drawn from `settingsControls.ts`, spread
`loadingButtonProps` from `ui/loadingButton.ts` rather than setting `disabled` for the wait, and
pair its `disabled:` classes with `aria-disabled:` ones (the settings classes already do). It then
keeps keyboard focus and ignores clicks until the request ends.

### Settings pages

Settings has its own primitives in
[frontend/src/components/settings/](../frontend/src/components/settings/), and a settings page is
built from them alone.

- **`SettingsCard`** is the only container: a title with optional description, status badge and
  header control, then rows and blocks separated by hairlines. No card nests inside another.
- **`SettingsRow`** puts a label and description on the left and one control on the right, and
  stacks them below 26rem of its own width. Pass `controlId` for a single control: the label
  becomes a real `<label>` for it, and the description takes the id `${controlId}-help` for the
  control's `aria-describedby`. A group of controls takes `labelId` and `aria-labelledby`
  instead. Rows carry no leading icon; category icons stay in the navigation.
- **`SettingsBlock`** holds what is not a label and a control: tables, meters, consoles, editors.
  With no label, description, aside or inset it renders its children straight into its padded
  cell, so a layout class passed to it, such as `space-y-4` or `flex`, reaches them. With a
  header, the content sits in its own wrapper under it.
- **Controls** take their classes from `settingsControls.ts` or the `ui/` primitives:
  `SETTINGS_INPUT_CLASS`, `SETTINGS_SELECT_CLASS` and `SETTINGS_TEXTAREA_CLASS` for fields, the
  three `SETTINGS_BUTTON_*` classes, `SETTINGS_VALUE_CLASS` for a read-only value, and
  `SETTINGS_CONTROL_END` for a switch or lone button held at the card's right edge.
  `settingsControls.test.ts` fails on a hand-written focus ring, a translucent fill on a field, and
  a `divide-y` with no colour token.

**Scope badges.** A setting belongs to the signed-in person unless marked **Everyone** (one value
for the whole installation) or **This browser** (kept in local storage). Declare scope once, as
`scope` on the setting's entry in `settingsRegistry.ts`; the card, row or labelled block that
renders the entry's id shows the badge, and so do search results. When every setting in a card
shares a scope, the card's own entry declares it and the badge goes on the title, and rows of the
same scope leave it to the card. A card that renders part of an entry under another id names the
entry with `scopeId`. Administrator-only pages carry no badges, since everything on them applies
to the whole installation. `settingsScope.test.tsx` keeps those pages unmarked, keeps browser
settings off the `Settings` object, and checks that every scoped entry is rendered.

## Typography

Geist and Geist Mono are served from files in the repository, so a build downloads nothing.
[frontend/src/app/fonts/geist.ts](../frontend/src/app/fonts/geist.ts) declares them with
`next/font/local`, and [geist.css](../frontend/src/app/fonts/geist.css) beside it sets
`--font-geist-sans` and `--font-geist-mono` on `body`, which `@theme inline` maps to Tailwind's
`--font-sans` and `--font-mono`. Geist Sans is set once on `body` in `globals.css`, so an element
needs `font-sans` only to override something back to the default. Apply Geist Mono with
`font-mono`.

Lucide is the only icon set.

### Font files

The files in `frontend/src/app/fonts/` are Google Fonts' build of Geist 1.800 and Geist Mono
1.701: the variable font (`wght` 100 to 900), split into Google's subsets. Both families have
latin, latin-ext, cyrillic, cyrillic-ext and vietnamese, and Geist Mono adds symbols2 for box
drawing. They are byte for byte the files `next/font/google` downloaded into the frontend image
before the app served its own, so the app renders as it did.

- **One `@font-face` per subset**, carrying Google's `unicode-range`, so a browser fetches a
  subset only when the page uses a character from it. Keep Google's order: where two ranges
  overlap, the face declared last wins, and that is latin.
- **Only latin is preloaded.**
- **The fallbacks are fixed values.** `geist.css` declares Arial with the metric overrides
  `next/font/google` generated for these families. `next/font/local` would measure the files
  instead and arrive at slightly different figures.
- **To update them**, request `https://fonts.googleapis.com/css2?family=Geist:wght@100..900`
  (and `Geist+Mono`) with a browser's user agent, so the response lists woff2 files, then replace
  one file per subset and copy each `unicode-range` into its call. `selfHostedFonts.test.ts` fails
  on a file that is not declared, a subset out of order and any import of `next/font/google`.

Geist is under the SIL Open Font License 1.1, in `OFL.txt` beside the files. The marketing site
serves its own latin files from `site/public/fonts/`.

### The heading scale

`density-heading-page` and `density-heading-section` carry the fluid heading sizes: one clamp
below the desktop breakpoint and another under compact or dense density. **Keep their font-size
rules outside the cascade layers.** Inside `@layer components` they lose to the `text-*`
utilities on the same element, because layer order beats selector specificity. Keep each clamp's
upper bound at the largest `text-*` size the class is paired with, so a clamp only ever steps a
heading down.

A heading that must fit its own content, such as the meeting title, uses `FitText`.

## Accessibility

The contrast target is WCAG 2.2 AA, enforced by
[frontend/scripts/check-contrast.mjs](../frontend/scripts/check-contrast.mjs). It parses the token
blocks in `tokens.css`, `palettes.css` and the marketing site's `site-tokens.css`, resolves
`var()`, composites translucent values over their backdrop stack, and measures a declared list of
pairings across twelve themes: the app's light and dark, each palette's light and dark, and the
site's light and dark. It runs as `npm run check:contrast` in the `Frontend lint` CI job and as
`node frontend/scripts/check-contrast.mjs` in the `Site build` job.

**A new token used as a foreground or background needs its pairings added to the script.** A
pairing that cannot be expressed in tokens means a token is missing; fix the tokens instead of
adding literals to the list.

The thresholds:

- **4.5:1** for text, including button labels and placeholder text.
- **3:1** for large text and for what identifies a component or its state: a control's boundary,
  a resting action or danger fill, a focus ring. Disabled control text is also held to 3:1. Card
  edges, dividers and the outline of a pill whose fill and label already carry the meaning are
  not held to 3:1.
- **1.15:1**, a house floor rather than a WCAG requirement, for decorative separators, chart and
  speaker colours (every series is directly labelled), and a float against the scrimmed content
  behind it. It stops a hairline drifting to invisible.

Beyond contrast, conformance is checked by hand. There is no `axe` or Playwright check, and the
few `jsx-a11y` rules in Next's ESLint config only warn, so verify keyboard reachability and
screen-reader behaviour when changing the interface. The primitives carry what they can:
a required label on `IconButton`, `aria-invalid` and `aria-describedby` on `Input` and `Select`,
`aria-busy` and `aria-disabled` on a loading `Button`, and the focus trap in `Modal`.

## The marketing site

The site in `site/` imports `frontend/src/app/tokens.css` from `Base.astro`, so it cannot disagree
with the app about what a token means. [SITE.md](SITE.md) owns the site's composition, voice,
claims and preview runbook; this section covers its tokens.

**Every site token family must answer to both theme inputs**: the OS `prefers-color-scheme` and
the explicit choice the header toggle stores and `Base.astro` stamps as `data-theme` before first
paint. `site/plugins/tokens-theme.mjs` generates both dark rules from one set of declarations:
`:root:not([data-theme="light"])` inside the media query and `:root[data-theme="dark"]` outside
it. Test the two combinations where the inputs disagree.

`site/src/styles/site-tokens.css` holds the families the app has no equivalent of: syntax
highlighting, the screenshot frame's chrome, the closer band's inverse and ghost buttons, the
marketing type scale, and the effects below. They are tokens so the contrast audit can measure
them. `Base.astro` must import the file as a module; the plugin fails the build if `site.css`
`@import`s it, since an inlined import never reaches the plugin.

**The quick-start code block is dark in both themes**, on its own `--code-bg` (`#111827`), with
one syntax palette. It is the one site surface that ignores the theme. It reaches 16.96:1 against
the light page and keeps the worst syntax pairing at 6.92:1.

The site departs from the flat canon in four site-only ways, all decoration over fills the gate
already checks:

- a halo and a 1px inset white top edge on the primary button (`--glow-action`, `--glow-inset`);
- a soft radial wash (`--hero-wash`) behind the hero and the agent band;
- a faint vertical gradient (`--band-wash`) on each `.band` section;
- an orange edge on every screenshot frame (`--frame-accent`), and a softer, wider lift on
  floating cards (`--lift`, `--lift-strong`) than the app's resting shadow.

The audit covers the site as two extra themes, `site-light` and `site-dark`, through
`SITE_PAIRINGS` in `check-contrast.mjs`. It has two blind spots:

- **It checks only the pairings it has been given.** New site furniture needs its pairing.
- **It measures values, never whether the rule holding them applies.** A dark block no visitor
  can reach still passes. Reachability is checked by the build (`tokens-theme.mjs` throws on a
  file it cannot parse) and in a browser across the four-way theme matrix in [SITE.md](SITE.md).

## Exported documents

PDF and Word exports are printed pages, so they take the light theme's values whatever theme the
reader uses in the app: `--foreground` for text, `--action` orange for the letterhead rule,
section underlines, table header rules and links, and the `--speaker-*` and `--chart-*` values for
series, keyed exactly as the Analytics tab keys them. The values are copied into
`backend/services/exports/theme.py`, because the backend cannot read `tokens.css`; change both
together.

The flat canon holds on the page. Type is Geist, SemiBold for headings, with Geist Mono for
transcript timestamps in the PDF.

Two rules are specific to print. A speaker's colour is used as is for a dot, a bar or a row
marker, but a speaker name set in that colour is darkened until it reaches 4.5:1 on white, so
yellow or lime still reads as the same person. With **Nojoin branding** turned off
under **Settings > Appearance > Exported files**, the orange becomes slate (`#334155`) and the logo and footer line
go, so a plain export carries no Nojoin colour.

## Related documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): where the web client sits in the system.
- [DEVELOPMENT.md](DEVELOPMENT.md): running and building the frontend, and the chart colour rules.
- [USAGE.md](USAGE.md): the interface as a user encounters it.

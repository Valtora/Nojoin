# Design System

The web client's visual language is defined once, as CSS custom properties in
[frontend/src/app/globals.css](../frontend/src/app/globals.css), and consumed through Tailwind
utility classes and a small set of primitive components. This document records the tokens, the
primitives, and the rules that govern them, so a change to the interface can be checked against an
intended system rather than against whatever the surrounding component happened to do.

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
document element, and a single block in `globals.css` re-declares the layout tokens at smaller
values. No component needs to know the density.

| Token group | Members |
| --- | --- |
| Root sizing | `--app-root-font-size` |
| Workspace | `--workspace-gap`, `--workspace-padding-x`, `--workspace-padding-x-md`, `--workspace-padding-y`, `--workspace-padding-y-md` |
| Workspace width | `--workspace-max-width`, `--workspace-max-width-wide`, `--workspace-max-width-feature` |
| Surfaces | `--surface-radius`, `--surface-radius-subtle`, `--surface-radius-panel`, `--surface-padding`, `--surface-padding-lg` |
| Controls | `--control-height-lg` |

Radii are one step tighter than the previous system: `--surface-radius` is 1.25rem, not 2rem. Cards
and controls stay at `rounded-lg`. Pills remain only for genuinely pill-shaped elements, meaning
tags, status badges and avatars.

Adding a hard-coded padding or radius to a component opts that component out of the density
setting. Use or extend the tokens instead. The radius tokens are also reachable as utilities:
`rounded-surface`, `rounded-surface-subtle`, `rounded-surface-panel`.

## Primitives

Shared components live in [frontend/src/components/ui/](../frontend/src/components/ui/) and consume
tokens exclusively. Build a surface out of these rather than out of raw elements.

| Primitive | Use for |
| --- | --- |
| `Button` | Any labelled action. `variant`: primary, secondary, ghost, danger. `size`: sm, md, lg. Handles `loading` by disabling itself and swapping the leading icon for a spinner, keeping the label so the control does not resize. |
| `IconButton` | An action with no label. `aria-label` is required, because it is the only name the control has. The smallest size renders a 16px glyph inside a 40px box, so the target stays reachable. |
| `Card` | The canonical resting surface. `interactive` switches on the tint-and-border hover treatment. |
| `Badge`, `StatusBadge` | Pills. `StatusBadge` maps a `RecordingStatus` to a tone, a glyph and a label. |
| `Input`, `Select` | Form fields. Both draw on the shared `fieldChrome` helper, so they cannot drift apart. `Select` is a native select, because the mobile pass wants the platform picker on a phone. |
| `Modal` | Every dialog. Wraps the Headless UI dialog, so the focus trap, scroll lock, Escape handling and portal come from a maintained implementation. Adds the plain scrim, the float surface and shadow, a token z-index, and the height cap and viewport gutter that stop a tall modal pushing its own actions below the fold on a phone. |
| `Switch`, `Tooltip`, `ModernDatePicker`, `MultiSelect` | Pre-existing widgets, restyled onto tokens. |

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

Lucide is the sole icon system.

## Accessibility

The contrast target is strict WCAG 2.2 AA, and it is enforced rather than reviewed.
[frontend/scripts/check-contrast.mjs](../frontend/scripts/check-contrast.mjs) parses the token
blocks out of `globals.css`, resolves `var()` indirection, composites translucent values over their
backdrop stack, and measures a declared list of pairings in both themes. It runs as
`npm run check:contrast` and as a step in the `Frontend lint` CI job.

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

## Related documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): where the web client sits in the system.
- [DEVELOPMENT.md](DEVELOPMENT.md): running and building the frontend.
- [USAGE.md](USAGE.md): the interface as a user encounters it.

# Design System

The web client's visual language is defined once, as CSS custom properties in
[frontend/src/app/globals.css](../frontend/src/app/globals.css), and consumed through Tailwind
utility classes. This document records the tokens and the rules that govern them, so a change to
the interface can be checked against an intended system rather than against whatever the
surrounding component happened to do.

## Themes

Light and dark are both first-class. Dark mode is class-based rather than media-query-based, so a
user can override their operating system preference:

```css
@custom-variant dark (&:where(.dark, .dark *));
```

Every token below is defined twice, on `:root` and on `.dark`. Components should reference the
token rather than branching on the theme themselves; `dark:` utility variants are for the cases a
token does not cover.

## Colour

### Brand

The primary action colour is orange-600 (`#ea580c`), with orange-700 (`#c2410c`) for hover and
active states. These are used through Tailwind's `orange-600` and `orange-700` classes rather than
as custom properties, and appear as literal hex values only where a utility class cannot reach,
such as scrollbar styling.

### Contrast tokens

These exist specifically to keep text and boundaries legible against both backgrounds. Prefer them
over raw Tailwind grey scales for body-adjacent text and borders.

| Token | Light | Dark |
| --- | --- | --- |
| `--foreground` | `#171717` | `#ededed` |
| `--contrast-muted` | `#374151` | `#e5e7eb` |
| `--contrast-helper` | `#4b5563` | `#d1d5db` |
| `--contrast-icon-muted` | `#52525b` | `#a1a1aa` |
| `--contrast-border` | `#cbd5e1` | `#4b5563` |
| `--contrast-border-strong` | `#94a3b8` | `#6b7280` |

Note that the light and dark values are not mirror images. Dark mode lifts muted text well above
its light-mode equivalent, because grey text loses legibility faster on a dark background than on
a light one.

### Surfaces

The surface stack is deliberately shallow, and the comment in `globals.css` states the constraint
that keeps it that way: there are exactly two levels, the page and one card on it, and nothing
nests further. The tokens must therefore stay monotonic, so a card is always a step away from the
page rather than a step back towards it. `--surface-inset` is for composite UI inside a card, such
as meters and consoles, and steps in the same direction.

| Token | Light | Dark |
| --- | --- | --- |
| `--surface-page` | `#f8fafc` | `#0a0a0a` |
| `--surface-card` | `#ffffff` | `#141414` |
| `--surface-card-border` | `rgba(203, 213, 225, 0.75)` | `rgba(75, 85, 99, 0.55)` |
| `--surface-divider` | `rgba(203, 213, 225, 0.55)` | `rgba(75, 85, 99, 0.35)` |
| `--surface-card-shadow` | `0 1px 2px 0 rgb(15 23 42 / 0.04)` | `none` |
| `--surface-inset` | `rgba(241, 245, 249, 0.75)` | `rgba(255, 255, 255, 0.03)` |

Dark mode drops card shadows entirely and separates surfaces by lightness instead, because a
shadow against a near-black page reads as a smudge rather than as elevation.

## Layout and density

Spacing, radii and control heights are tokens rather than per-component values, which is what
makes the density setting possible. `ViewportDensityProvider` sets `data-ui-density="compact"` on
the document element, and a single block in `globals.css` re-declares the layout tokens at smaller
values. No component needs to know the density.

| Token group | Members |
| --- | --- |
| Root sizing | `--app-root-font-size` |
| Workspace | `--workspace-gap`, `--workspace-padding-x`, `--workspace-padding-x-md`, `--workspace-padding-y`, `--workspace-padding-y-md` |
| Workspace width | `--workspace-max-width`, `--workspace-max-width-wide`, `--workspace-max-width-feature` |
| Surfaces | `--surface-radius`, `--surface-radius-subtle`, `--surface-radius-panel`, `--surface-padding`, `--surface-padding-lg` |
| Controls | `--control-height-lg` |

Adding a hard-coded padding or radius to a component opts that component out of the density
setting. Use or extend the tokens instead.

## Typography

`Geist` and `Geist_Mono` are loaded in [frontend/src/app/layout.tsx](../frontend/src/app/layout.tsx)
and exposed as `--font-geist-sans` and `--font-geist-mono`, which `@theme inline` maps to Tailwind's
`--font-sans` and `--font-mono`.

Body text does not currently use them. `globals.css` sets `body { font-family: Arial, Helvetica,
sans-serif; }`, so Geist applies only where a `font-sans` or `font-mono` utility is used
explicitly.

## Accessibility

The contrast tokens above exist to keep text legible in both themes, and the primary action colour
was chosen for contrast against white. Beyond that, conformance is a matter of review rather than
of tooling: the project runs no automated accessibility checks, so there is no `axe`, `jsx-a11y`
or similar gate in CI or the test suite. Treat contrast and keyboard reachability as things to
verify by hand when changing the interface.

## Related documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): where the web client sits in the system.
- [DEVELOPMENT.md](DEVELOPMENT.md): running and building the frontend.
- [USAGE.md](USAGE.md): the interface as a user encounters it.

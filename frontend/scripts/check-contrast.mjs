#!/usr/bin/env node
/**
 * Token contrast audit.
 *
 * Reads the design tokens straight out of tokens.css and the marketing site's
 * site-tokens.css, and measures the pairings declared below against WCAG 2.2
 * AA across four themes: the app's light and dark, and the site's light and
 * dark, which are the app themes plus the site-only families. It
 * exists because contrast is a property
 * of a pair, not of a colour: a token can only be judged against the thing it is
 * actually drawn on, and that relationship lives nowhere in CSS. Declaring the
 * pairs here makes them reviewable, and makes a regression a failed build rather
 * than something noticed in a screenshot months later.
 *
 * A translucent token is composited over its backdrop stack before it is
 * measured, so `--action-tint` at 18% alpha is judged as what the eye receives
 * rather than as the value written in the file.
 *
 * A pairing that cannot be expressed in tokens is a smell in the tokens. Fix the
 * tokens rather than widening this list with literals.
 */

import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
/**
 * The design tokens, extracted from globals.css so the marketing site can
 * import the same file. The :root, .dark and density blocks live here; the
 * app furniture that stayed in globals.css declares no colour tokens.
 */
const TOKENS = resolve(ROOT, "src/app/tokens.css");
/**
 * The marketing site's own token families: syntax highlighting, the screenshot
 * frame chrome, and the closer-band buttons. Everything else the site draws
 * comes from tokens.css above, so the site themes below are the app themes
 * plus this file. The light block is the top-level :root; the dark block is
 * the :root inside the prefers-color-scheme media query.
 */
const SITE_TOKENS = resolve(ROOT, "../site/src/styles/site-tokens.css");

/**
 * Thresholds.
 *
 * AA_NON_TEXT is SC 1.4.11, and it is narrower than "every border": it covers
 * the visual information *required to identify* a component or its state. A
 * control's boundary qualifies, because without it there is no control. A card
 * edge, a divider, or the outline of a pill whose fill and label already carry
 * the meaning does not, and holding those to 3:1 would replace the hairlines
 * this design is built on with wireframe outlines.
 *
 * HAIRLINE is this project's own floor, not WCAG's. A decorative separator is
 * exempt from the standard but still has to be visible, so a token that has
 * drifted to invisible fails here rather than passing by exemption.
 */
const AA_TEXT = 4.5;
const AA_LARGE = 3;
const AA_NON_TEXT = 3;
const HAIRLINE = 1.15;

/**
 * The pairings under audit.
 *
 * `bg` is a stack, bottom first: each layer is composited onto the one beneath
 * it, so the last entry is what the foreground actually sits on. `fg` is a
 * single token, itself composited onto the flattened stack when translucent.
 *
 * `min` is the threshold this pairing has to clear: AA_TEXT for anything read as
 * body or label text, AA_LARGE for headings at 18.66px bold or 24px and up, and
 * AA_NON_TEXT for borders, focus rings, and other component boundaries.
 */
const PAIRINGS = [
  // Body text on the two surfaces of the stack.
  { label: "body text on page", fg: "foreground", bg: ["surface-page"], min: AA_TEXT },
  { label: "body text on card", fg: "foreground", bg: ["surface-page", "surface-card"], min: AA_TEXT },
  { label: "body text on inset", fg: "foreground", bg: ["surface-page", "surface-card", "surface-inset"], min: AA_TEXT },
  { label: "muted text on card", fg: "contrast-muted", bg: ["surface-page", "surface-card"], min: AA_TEXT },
  { label: "helper text on card", fg: "contrast-helper", bg: ["surface-page", "surface-card"], min: AA_TEXT },
  { label: "helper text on inset", fg: "contrast-helper", bg: ["surface-page", "surface-card", "surface-inset"], min: AA_TEXT },
  { label: "muted icon on card", fg: "contrast-icon-muted", bg: ["surface-page", "surface-card"], min: AA_NON_TEXT },
  { label: "muted icon on page", fg: "contrast-icon-muted", bg: ["surface-page"], min: AA_NON_TEXT },

  // Decorative boundaries: visible, but not component-identifying.
  { label: "card border vs page", fg: "surface-card-border", bg: ["surface-page"], min: HAIRLINE },
  { label: "card border vs card", fg: "surface-card-border", bg: ["surface-page", "surface-card"], min: HAIRLINE },
  { label: "divider vs card", fg: "surface-divider", bg: ["surface-page", "surface-card"], min: HAIRLINE },
  { label: "contrast border vs card", fg: "contrast-border", bg: ["surface-page", "surface-card"], min: HAIRLINE },
  { label: "strong border vs card", fg: "contrast-border-strong", bg: ["surface-page", "surface-card"], min: HAIRLINE },

  // Form controls: the boundary is what identifies them, so 3:1 applies.
  { label: "control border vs card", fg: "control-border", bg: ["surface-page", "surface-card"], min: AA_NON_TEXT },
  { label: "control border vs page", fg: "control-border", bg: ["surface-page"], min: AA_NON_TEXT },
  { label: "control border vs inset", fg: "control-border", bg: ["surface-page", "surface-card", "surface-inset"], min: AA_NON_TEXT },
  { label: "control text on control", fg: "foreground", bg: ["surface-page", "surface-card", "control-bg"], min: AA_TEXT },
  { label: "placeholder on control", fg: "control-placeholder", bg: ["surface-page", "surface-card", "control-bg"], min: AA_TEXT },
  { label: "disabled text on disabled control", fg: "control-disabled-fg", bg: ["surface-page", "surface-card", "control-disabled-bg"], min: AA_LARGE },
  { label: "disabled border vs card", fg: "control-disabled-border", bg: ["surface-page", "surface-card"], min: HAIRLINE },

  // Action family. The label on a primary fill is text and answers to 4.5. The
  // resting fill answers to 3:1 as a component boundary; hover and active do
  // not, because by then the control has already been identified.
  { label: "action label on action fill", fg: "action-on", bg: ["action"], min: AA_TEXT },
  { label: "action muted label on action fill", fg: "action-on-muted", bg: ["action"], min: AA_TEXT },
  { label: "action label on hover fill", fg: "action-on", bg: ["action-hover"], min: AA_TEXT },
  { label: "action label on active fill", fg: "action-on", bg: ["action-active"], min: AA_TEXT },
  { label: "action fill vs page", fg: "action", bg: ["surface-page"], min: AA_NON_TEXT },
  { label: "action fill vs card", fg: "action", bg: ["surface-page", "surface-card"], min: AA_NON_TEXT },
  { label: "action text on page", fg: "action-text", bg: ["surface-page"], min: AA_TEXT },
  { label: "action text on card", fg: "action-text", bg: ["surface-page", "surface-card"], min: AA_TEXT },
  { label: "action text on inset", fg: "action-text", bg: ["surface-page", "surface-card", "surface-inset"], min: AA_TEXT },
  { label: "action text hover on card", fg: "action-text-hover", bg: ["surface-page", "surface-card"], min: AA_TEXT },
  { label: "tint text on tint (on card)", fg: "action-tint-fg", bg: ["surface-page", "surface-card", "action-tint"], min: AA_TEXT },
  { label: "tint text on hover tint (on card)", fg: "action-tint-fg", bg: ["surface-page", "surface-card", "action-tint-hover"], min: AA_TEXT },
  { label: "action border vs card", fg: "action-border", bg: ["surface-page", "surface-card"], min: HAIRLINE },

  // Danger family, held to the same rules as the action family.
  { label: "danger label on danger fill", fg: "danger-on", bg: ["danger"], min: AA_TEXT },
  { label: "danger label on hover fill", fg: "danger-on", bg: ["danger-hover"], min: AA_TEXT },
  { label: "danger label on active fill", fg: "danger-on", bg: ["danger-active"], min: AA_TEXT },
  { label: "danger fill vs page", fg: "danger", bg: ["surface-page"], min: AA_NON_TEXT },
  { label: "danger fill vs card", fg: "danger", bg: ["surface-page", "surface-card"], min: AA_NON_TEXT },
  { label: "danger text on card", fg: "danger-text", bg: ["surface-page", "surface-card"], min: AA_TEXT },
  { label: "danger text on page", fg: "danger-text", bg: ["surface-page"], min: AA_TEXT },
  { label: "danger text on float", fg: "danger-text", bg: ["surface-float"], min: AA_TEXT },
  { label: "danger text hover on card", fg: "danger-text-hover", bg: ["surface-page", "surface-card"], min: AA_TEXT },
  { label: "focus ring vs page", fg: "focus-ring", bg: ["surface-page"], min: AA_NON_TEXT },
  { label: "focus ring vs card", fg: "focus-ring", bg: ["surface-page", "surface-card"], min: AA_NON_TEXT },

  // Floats. Modals, dropdowns, popovers and toasts share one surface token.
  { label: "body text on float", fg: "foreground", bg: ["surface-float"], min: AA_TEXT },
  { label: "muted text on float", fg: "contrast-muted", bg: ["surface-float"], min: AA_TEXT },
  { label: "helper text on float", fg: "contrast-helper", bg: ["surface-float"], min: AA_TEXT },
  { label: "action text on float", fg: "action-text", bg: ["surface-float"], min: AA_TEXT },
  { label: "control border vs float", fg: "control-border", bg: ["surface-float"], min: AA_NON_TEXT },
  { label: "focus ring vs float", fg: "focus-ring", bg: ["surface-float"], min: AA_NON_TEXT },
  { label: "float border vs float", fg: "surface-float-border", bg: ["surface-float"], min: HAIRLINE },
  { label: "tooltip text on tooltip", fg: "tooltip-fg", bg: ["tooltip-bg"], min: AA_TEXT },
  { label: "tooltip vs card", fg: "tooltip-bg", bg: ["surface-page", "surface-card"], min: AA_NON_TEXT },

  // Chart series. HAIRLINE rather than AA_NON_TEXT, and the distinction is
  // load-bearing: 1.4.11 covers what is *required* to identify a component,
  // and a series fill is not that here. Every series carries a direct label
  // and a legend entry, and the same figures are available as text, so colour
  // reinforces identity rather than conveying it. The floor these are held to
  // is visibility. If a chart is ever added that drops its labels, these
  // pairings move to AA_NON_TEXT and three of the light steps stop passing.
  { label: "chart series 1 vs card", fg: "chart-1", bg: ["surface-page", "surface-card"], min: HAIRLINE },
  { label: "chart series 2 vs card", fg: "chart-2", bg: ["surface-page", "surface-card"], min: HAIRLINE },
  { label: "chart series 3 vs card", fg: "chart-3", bg: ["surface-page", "surface-card"], min: HAIRLINE },
  { label: "chart series 4 vs card", fg: "chart-4", bg: ["surface-page", "surface-card"], min: HAIRLINE },
  { label: "chart series 5 vs card", fg: "chart-5", bg: ["surface-page", "surface-card"], min: HAIRLINE },
  { label: "chart series 6 vs card", fg: "chart-6", bg: ["surface-page", "surface-card"], min: HAIRLINE },
  { label: "chart series 7 vs card", fg: "chart-7", bg: ["surface-page", "surface-card"], min: HAIRLINE },
  { label: "chart series 8 vs card", fg: "chart-8", bg: ["surface-page", "surface-card"], min: HAIRLINE },
  { label: "chart other vs card", fg: "chart-other", bg: ["surface-page", "surface-card"], min: HAIRLINE },
  { label: "chart grid vs card", fg: "chart-grid", bg: ["surface-page", "surface-card"], min: HAIRLINE },

  // Meeting speaker colours, which the analytics charts draw a speaker's bars
  // and bands in so that the person is one colour across the whole meeting
  // view. Same threshold and same reasoning as the chart slots above: these
  // are series fills beside a direct label, never the sole carrier of
  // identity. They are one value in both themes on purpose -- they mirror the
  // speaker panel's dots, which are also one value -- so each is measured
  // against both surfaces of the stack in each theme.
  ...[
    "red", "rose", "pink", "orange", "amber", "yellow", "lime", "green",
    "emerald", "teal", "cyan", "sky", "blue", "indigo", "violet", "purple",
    "fuchsia",
  ].map((key) => ({
    label: `speaker ${key} vs card`,
    fg: `speaker-${key}`,
    bg: ["surface-page", "surface-card"],
    min: HAIRLINE,
  })),

  // Rails. Their own surface, so nothing here is measured against the page.
  { label: "rail text on rail", fg: "rail-fg", bg: ["rail-bg"], min: AA_TEXT },
  { label: "rail muted text on rail", fg: "rail-fg-muted", bg: ["rail-bg"], min: AA_TEXT },
  { label: "rail text on hovered item", fg: "rail-fg", bg: ["rail-bg", "rail-item-hover"], min: AA_TEXT },
  { label: "rail active text on active item", fg: "rail-item-active-fg", bg: ["rail-bg", "rail-item-active"], min: AA_TEXT },
  { label: "rail border vs rail", fg: "rail-border", bg: ["rail-bg"], min: HAIRLINE },
  { label: "rail border vs page", fg: "rail-border", bg: ["surface-page"], min: HAIRLINE },
  { label: "focus ring vs rail", fg: "focus-ring", bg: ["rail-bg"], min: AA_NON_TEXT },

  // Settings tabs, kept because they are the surface the flat canon came from.
  { label: "settings active tab text", fg: "settings-tab-active-text", bg: ["surface-page", "settings-tab-active-bg"], min: AA_TEXT },
  { label: "settings idle tab text", fg: "settings-tab-idle-text", bg: ["surface-page"], min: AA_TEXT },
  { label: "settings idle tab text on hover", fg: "settings-tab-idle-text", bg: ["surface-page", "settings-tab-idle-hover"], min: AA_TEXT },
  { label: "settings active tab border", fg: "settings-tab-active-border", bg: ["surface-page", "settings-tab-active-bg"], min: HAIRLINE },

  // Status pills, on a card and on the page, since both placements occur.
  ...["neutral", "info", "success", "warning", "danger"].flatMap((tone) => [
    {
      label: `status ${tone} text on card`,
      fg: `status-${tone}-fg`,
      bg: ["surface-page", "surface-card", `status-${tone}-bg`],
      min: AA_TEXT,
    },
    {
      label: `status ${tone} text on page`,
      fg: `status-${tone}-fg`,
      bg: ["surface-page", `status-${tone}-bg`],
      min: AA_TEXT,
    },
    {
      label: `status ${tone} border on card`,
      fg: `status-${tone}-border`,
      bg: ["surface-page", "surface-card", `status-${tone}-bg`],
      min: HAIRLINE,
    },
    // The pill's fill is deliberately not asserted. A 50-level tint on a white
    // card is about 1.05:1 by design: the tone is carried by the label and the
    // outline, and forcing the fill to separate would turn every pill into a
    // block of colour.
  ]),
];

/**
 * The marketing site's pairings, audited in both themes.
 *
 * The site consumes the app's tokens plus the site-only families, so most of
 * these read like the app list. What differs is the furniture: no modals, no
 * form controls beyond two buttons, no status pills — and a syntax
 * highlighter, a screenshot frame, and a full-bleed orange closer band, none
 * of which the app has an equivalent of.
 */
const SITE_PAIRINGS = [
  // Text on the alternating page and card bands.
  { label: "heading on page", fg: "foreground", bg: ["surface-page"], min: AA_TEXT },
  { label: "heading on card band", fg: "foreground", bg: ["surface-page", "surface-card"], min: AA_TEXT },
  { label: "body text on page", fg: "contrast-muted", bg: ["surface-page"], min: AA_TEXT },
  { label: "body text on card band", fg: "contrast-muted", bg: ["surface-page", "surface-card"], min: AA_TEXT },
  { label: "lead text on page", fg: "contrast-helper", bg: ["surface-page"], min: AA_TEXT },
  { label: "lead text on card band", fg: "contrast-helper", bg: ["surface-page", "surface-card"], min: AA_TEXT },
  { label: "zebra row text on inset", fg: "contrast-muted", bg: ["surface-page", "surface-card", "surface-inset"], min: AA_TEXT },
  // The agent-flow card's actor pills. The Nojoin pill is the action fill,
  // already covered by the primary CTA rows below; the "you" pill reuses the
  // secondary control's text and border. Only the assistant pill is new:
  // foreground on the inset surface, inside a card, on the page band.
  { label: "actor pill label on inset", fg: "foreground", bg: ["surface-page", "surface-card", "surface-inset"], min: AA_TEXT },
  { label: "actor pill border vs card", fg: "control-border", bg: ["surface-page", "surface-card"], min: AA_NON_TEXT },
  // The tool map's per-group count, which is the action colour as small text.
  { label: "tool group count on card", fg: "action-text", bg: ["surface-page", "surface-card"], min: AA_TEXT },

  // Hairlines and boundaries.
  { label: "card border vs page", fg: "surface-card-border", bg: ["surface-page"], min: HAIRLINE },
  { label: "divider vs card", fg: "surface-divider", bg: ["surface-page", "surface-card"], min: HAIRLINE },
  { label: "band border vs page", fg: "surface-divider", bg: ["surface-page"], min: HAIRLINE },

  // Links, eyebrows, and the accent.
  { label: "link on page", fg: "action-text", bg: ["surface-page"], min: AA_TEXT },
  { label: "link on card band", fg: "action-text", bg: ["surface-page", "surface-card"], min: AA_TEXT },
  { label: "link hover on card band", fg: "action-text-hover", bg: ["surface-page", "surface-card"], min: AA_TEXT },

  // The hero CTA pair. The primary's fill identifies it, so 3:1 applies to
  // the resting fill; the secondary is identified by its control border.
  { label: "primary CTA label", fg: "action-on", bg: ["action"], min: AA_TEXT },
  { label: "primary CTA label on hover", fg: "action-on", bg: ["action-hover"], min: AA_TEXT },
  { label: "primary CTA fill vs page", fg: "action", bg: ["surface-page"], min: AA_NON_TEXT },
  { label: "primary CTA fill vs card band", fg: "action", bg: ["surface-page", "surface-card"], min: AA_NON_TEXT },
  { label: "secondary CTA text", fg: "contrast-muted", bg: ["surface-page", "surface-card"], min: AA_TEXT },
  { label: "secondary CTA text on hover", fg: "contrast-muted", bg: ["surface-page", "surface-card", "surface-inset"], min: AA_TEXT },
  { label: "secondary CTA border vs page", fg: "control-border", bg: ["surface-page"], min: AA_NON_TEXT },
  { label: "secondary CTA border vs card band", fg: "control-border", bg: ["surface-page", "surface-card"], min: AA_NON_TEXT },
  { label: "focus ring vs page", fg: "focus-ring", bg: ["surface-page"], min: AA_NON_TEXT },
  { label: "focus ring vs card band", fg: "focus-ring", bg: ["surface-page", "surface-card"], min: AA_NON_TEXT },

  // The screenshot frame. The chrome strip and dots are decoration inside the
  // framed card; the URL text is read, so it answers as text.
  { label: "frame url text on url pill", fg: "contrast-icon-muted", bg: ["surface-card", "frame-chrome", "surface-page"], min: AA_TEXT },

  // The orange closer band. The standard focus ring vanishes on this fill, so
  // the site inverts focus to the label colour there, and the gate holds that
  // substitute to the same 3:1 the ring answers to everywhere else.
  { label: "closer heading on fill", fg: "action-on", bg: ["action"], min: AA_TEXT },
  { label: "closer subhead on fill", fg: "action-on-muted", bg: ["action"], min: AA_TEXT },
  { label: "inverse button label", fg: "btn-inverse-fg", bg: ["action", "btn-inverse-bg"], min: AA_TEXT },
  { label: "inverse button label on hover", fg: "btn-inverse-fg", bg: ["action", "btn-inverse-bg-hover"], min: AA_TEXT },
  { label: "ghost button border vs fill", fg: "btn-ghost-border", bg: ["action"], min: AA_NON_TEXT },
  { label: "ghost button label on hover tint", fg: "action-on", bg: ["action", "btn-ghost-hover-bg"], min: AA_TEXT },
  { label: "closer focus vs fill", fg: "action-on", bg: ["action"], min: AA_NON_TEXT },

  // The selective highlight: lead-register text on the flat action tint.
  { label: "highlight text on tint", fg: "contrast-helper", bg: ["surface-page", "action-tint"], min: AA_TEXT },
  { label: "highlight strong text on tint", fg: "foreground", bg: ["surface-page", "action-tint"], min: AA_TEXT },

  // Footer text sits directly on the page.
  { label: "footer text on page", fg: "contrast-icon-muted", bg: ["surface-page"], min: AA_TEXT },
  { label: "footer link hover on page", fg: "foreground", bg: ["surface-page"], min: AA_TEXT },

  // Syntax highlighting. Every token is read as code, so all of it is text.
  // --code-bg is opaque and dark in both themes, so this pairing measures the
  // same numbers under site-light and site-dark by design; the block does not
  // follow the theme. It stays declared as a stack so a later translucent
  // value cannot silently stop being composited.
  ...["fg", "comment", "keyword", "name", "string", "punct"].map((part) => ({
    label: `code ${part} on code surface`,
    fg: `code-${part}`,
    bg: ["surface-page", "surface-card", "code-bg"],
    min: AA_TEXT,
  })),
];

/**
 * Overlay separation.
 *
 * The scrim itself is not measurable as a contrast requirement: WCAG says
 * nothing about it, and in dark mode a black dim over a near-black page changes
 * almost nothing (1.04:1), which would fail any threshold set on the scrim
 * alone while describing a real design that works. What has to hold is that the
 * floating panel separates from the content the scrim has pushed back, so that
 * is what is asserted, with the float shadow and border carrying the rest.
 */
const FLOAT_SEPARATION = HAIRLINE;

/* ------------------------------------------------------------------ parsing */

/**
 * Pull `--name: value;` declarations out of one top-level CSS block.
 *
 * The selector is anchored to the start of a line and has to be followed by its
 * own brace. That mattered most when this read globals.css, where `.dark` also
 * appeared inside a @custom-variant and in component overrides; tokens.css is
 * cleaner, but the anchoring stays because a substring match that finds the
 * wrong block makes the audit measure the wrong theme and pass anyway.
 */
function parseBlock(css, selector) {
  const anchored = new RegExp(`^${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{`, "m");
  const match = anchored.exec(css);
  if (match === null) throw new Error(`selector ${selector} not found`);
  const start = match.index;
  const open = css.indexOf("{", start);
  let depth = 0;
  let end = open;
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    else if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  const body = css.slice(open + 1, end);
  const tokens = {};
  for (const [, name, value] of body.matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) {
    tokens[name] = value.trim();
  }
  return tokens;
}

/**
 * Pull the `:root` block from inside the site's prefers-color-scheme media
 * query. The site has no JavaScript, so its dark theme lives under a media
 * query rather than a class; the block is indented, which the anchored parser
 * above deliberately refuses to match, so it gets its own entry point.
 */
function parseMediaDarkRoot(css) {
  const media = /^@media \(prefers-color-scheme: dark\)\s*\{/m.exec(css);
  if (media === null) throw new Error("no prefers-color-scheme dark block found");
  const open = css.indexOf("{", media.index);
  let depth = 0;
  let end = open;
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    else if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  const inner = css.slice(open + 1, end);
  const root = /:root\s*\{/.exec(inner);
  if (root === null) throw new Error("no :root inside the dark media block");
  const rootOpen = inner.indexOf("{", root.index);
  const rootEnd = inner.indexOf("}", rootOpen);
  const tokens = {};
  for (const [, name, value] of inner.slice(rootOpen + 1, rootEnd).matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) {
    tokens[name] = value.trim();
  }
  return tokens;
}

/** Resolve var() indirection within a theme, falling back to the light theme. */
function resolveValue(name, theme, base, seen = new Set()) {
  if (seen.has(name)) throw new Error(`circular token reference at --${name}`);
  seen.add(name);
  const raw = theme[name] ?? base[name];
  if (raw === undefined) throw new Error(`unknown token --${name}`);
  const varMatch = raw.match(/^var\(\s*--([\w-]+)\s*(?:,[^)]*)?\)$/);
  if (varMatch) return resolveValue(varMatch[1], theme, base, seen);
  return raw;
}

/** Parse a CSS colour into [r, g, b, a]. Only the forms the tokens use. */
function parseColour(value, token) {
  const v = value.trim();

  const hex = v.match(/^#([0-9a-f]{3,8})$/i);
  if (hex) {
    let h = hex[1];
    if (h.length === 3 || h.length === 4) h = h.split("").map((c) => c + c).join("");
    const n = (i) => parseInt(h.slice(i, i + 2), 16);
    return [n(0), n(2), n(4), h.length === 8 ? n(6) / 255 : 1];
  }

  // rgb(r, g, b) / rgba(r, g, b, a) / rgb(r g b / a)
  const fn = v.match(/^rgba?\(([^)]+)\)$/i);
  if (fn) {
    const [rgbPart, alphaPart] = fn[1].split("/");
    const parts = rgbPart.trim().split(/[\s,]+/).filter(Boolean);
    const alpha = alphaPart !== undefined ? Number(alphaPart) : parts[3] !== undefined ? Number(parts[3]) : 1;
    return [Number(parts[0]), Number(parts[1]), Number(parts[2]), alpha];
  }

  throw new Error(`cannot parse colour "${value}" for --${token}`);
}

/* ---------------------------------------------------------------- measuring */

/** Composite a colour with alpha onto an opaque backdrop. */
function composite([r, g, b, a], [br, bg, bb]) {
  return [r * a + br * (1 - a), g * a + bg * (1 - a), b * a + bb * (1 - a), 1];
}

function relativeLuminance([r, g, b]) {
  const channel = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrastRatio(a, b) {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

function toHex([r, g, b]) {
  return "#" + [r, g, b].map((c) => Math.round(c).toString(16).padStart(2, "0")).join("");
}

/** Flatten a bottom-first stack of tokens into one opaque colour. */
function flattenStack(stack, theme, base) {
  let current = null;
  for (const token of stack) {
    const colour = parseColour(resolveValue(token, theme, base), token);
    if (current === null) {
      if (colour[3] < 1) throw new Error(`bottom of stack --${token} must be opaque`);
      current = colour;
    } else {
      current = composite(colour, current);
    }
  }
  return current;
}

/* -------------------------------------------------------------------- audit */

const css = await readFile(TOKENS, "utf8");
const light = parseBlock(css, ":root");
const dark = parseBlock(css, ".dark");

const siteCss = await readFile(SITE_TOKENS, "utf8");
const siteLight = parseBlock(siteCss, ":root");
const siteDark = parseMediaDarkRoot(siteCss);

/**
 * `base` is the fallback a token resolves against when the block does not
 * declare it. The app's `.dark` block only declares what changes, so it falls
 * back to `:root`. The site themes are the app themes overlaid with the
 * site-only families: at build time the site imports tokens.css and
 * site-tokens.css, both put through the tokens-theme plugin, and these merges
 * mirror that cascade exactly.
 *
 * KNOWN BLIND SPOT. This audit measures VALUES. It has no model of selector
 * resolution, so it cannot tell whether the block a value sits in ever applies
 * to anything. Both site blocks passed for the whole time site-tokens.css had
 * no `data-theme` handling at all -- the dark values were correct, and
 * unreachable for any visitor who chose light on a machine set to dark. Adding
 * a pairing here proves a colour pair is legible, never that a visitor can
 * reach it. Reaching it is the build's job (see site/plugins/tokens-theme.mjs)
 * and the browser's (see the four-way theme matrix in docs/SITE.md).
 *
 * `floats` is off for the site because it has no floating elements: it
 * carries no modals, so it carries no shadows at all.
 */
const themes = [
  { name: "light", tokens: light, base: light, pairings: PAIRINGS, floats: true },
  { name: "dark", tokens: dark, base: light, pairings: PAIRINGS, floats: true },
  {
    name: "site-light",
    tokens: { ...light, ...siteLight },
    base: { ...light, ...siteLight },
    pairings: SITE_PAIRINGS,
    floats: false,
  },
  {
    name: "site-dark",
    tokens: { ...dark, ...siteDark },
    base: { ...light, ...siteLight },
    pairings: SITE_PAIRINGS,
    floats: false,
  },
];

const failures = [];
const results = [];

for (const theme of themes) {
  const base = theme.base;
  for (const pairing of theme.pairings) {
    let ratio;
    try {
      const bg = flattenStack(pairing.bg, theme.tokens, base);
      const fgRaw = parseColour(resolveValue(pairing.fg, theme.tokens, base), pairing.fg);
      const fg = fgRaw[3] < 1 ? composite(fgRaw, bg) : fgRaw;
      ratio = contrastRatio(fg, bg);
      results.push({
        theme: theme.name,
        label: pairing.label,
        ratio,
        min: pairing.min,
        detail: `${toHex(fg)} on ${toHex(bg)}`,
        pass: ratio >= pairing.min,
      });
    } catch (error) {
      failures.push(`${theme.name}: ${pairing.label}: ${error.message}`);
      continue;
    }
    if (ratio < pairing.min) {
      failures.push(
        `${theme.name}: ${pairing.label} is ${ratio.toFixed(2)}:1, needs ${pairing.min}:1`,
      );
    }
  }

  if (!theme.floats) continue;

  // A float has to separate from the scrimmed card behind it, which is the
  // worst case: a card is the lightest thing the scrim ever covers.
  const behind = flattenStack(["surface-page", "surface-card", "scrim"], theme.tokens, base);
  const float = flattenStack(["surface-float"], theme.tokens, base);
  const separation = contrastRatio(float, behind);
  const separationPass = separation >= FLOAT_SEPARATION;
  results.push({
    theme: theme.name,
    label: "float separates from scrimmed content",
    ratio: separation,
    min: FLOAT_SEPARATION,
    detail: `${toHex(float)} over ${toHex(behind)}`,
    pass: separationPass,
  });
  if (!separationPass) {
    failures.push(
      `${theme.name}: float separates from scrimmed content by only ${separation.toFixed(2)}:1, needs ${FLOAT_SEPARATION}:1`,
    );
  }
}

const verbose = process.argv.includes("--verbose");
if (verbose) {
  for (const theme of themes) {
    console.log(`\n${theme.name}`);
    for (const r of results.filter((x) => x.theme === theme.name)) {
      const mark = r.pass ? "pass" : "FAIL";
      console.log(
        `  ${mark}  ${r.ratio.toFixed(2).padStart(6)}:1  (min ${String(r.min).padEnd(3)})  ${r.label.padEnd(38)} ${r.detail}`,
      );
    }
  }
  console.log("");
}

if (failures.length > 0) {
  console.error(`\ncontrast audit failed: ${failures.length} of ${results.length} pairings\n`);
  for (const failure of failures) console.error(`  ${failure}`);
  console.error("\nRun with --verbose to see every measured pairing.\n");
  process.exit(1);
}

console.log(`contrast audit passed: ${results.length} pairings across ${themes.length} themes`);

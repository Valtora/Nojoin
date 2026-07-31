#!/usr/bin/env node
/**
 * Token contrast audit.
 *
 * Reads the design tokens straight out of globals.css and measures the pairings
 * declared below against WCAG 2.2 AA. It exists because contrast is a property
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
const GLOBALS = resolve(ROOT, "src/app/globals.css");

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
 * own brace, because `.dark` also appears inside the @custom-variant on line 6
 * and inside every `.dark .react-datepicker__…` override further down. A plain
 * substring search finds the variant first and then reads the *next* block,
 * which is `:root`, so the dark audit measures the light theme and passes.
 */
function parseBlock(css, selector) {
  const anchored = new RegExp(`^${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{`, "m");
  const match = anchored.exec(css);
  if (match === null) throw new Error(`selector ${selector} not found in globals.css`);
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

const css = await readFile(GLOBALS, "utf8");
const light = parseBlock(css, ":root");
const dark = parseBlock(css, ".dark");

const themes = [
  { name: "light", tokens: light },
  { name: "dark", tokens: dark },
];

const failures = [];
const results = [];

for (const theme of themes) {
  for (const pairing of PAIRINGS) {
    let ratio;
    try {
      const bg = flattenStack(pairing.bg, theme.tokens, light);
      const fgRaw = parseColour(resolveValue(pairing.fg, theme.tokens, light), pairing.fg);
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

  // A float has to separate from the scrimmed card behind it, which is the
  // worst case: a card is the lightest thing the scrim ever covers.
  const behind = flattenStack(["surface-page", "surface-card", "scrim"], theme.tokens, light);
  const float = flattenStack(["surface-float"], theme.tokens, light);
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

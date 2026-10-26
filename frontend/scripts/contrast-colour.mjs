/** Token extraction, bounded CSS colour evaluation and contrast maths shared by the audit. */
export function parseBlock(css, selector) {
  const anchored = new RegExp(`^${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{`, "m");
  const match = anchored.exec(css);
  if (match === null) throw new Error(`selector ${selector} not found`);
  const open = css.indexOf("{", match.index);
  let depth = 0;
  for (let end = open; end < css.length; end += 1) {
    if (css[end] === "{") depth += 1;
    else if (css[end] === "}" && --depth === 0) {
      return Object.fromEntries([...css.slice(open + 1, end).matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)]
        .map(([, name, value]) => [name, value.trim()]));
    }
  }
  throw new Error(`unterminated selector ${selector}`);
}

/** Parse only the numeric CSS forms used by the palette, rejecting unsupported syntax. */
export function parseColour(value, token) {
  const v = value.trim();
  const hex = v.match(/^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i);
  if (hex) {
    let h = hex[1];
    if (h.length < 5) h = h.split("").map((c) => c + c).join("");
    const n = (i) => parseInt(h.slice(i, i + 2), 16);
    return [n(0), n(2), n(4), h.length === 8 ? n(6) / 255 : 1];
  }

  const fn = v.match(/^rgba?\(([^)]+)\)$/i);
  if (fn) {
    const [rgbPart, alphaPart, extra] = fn[1].split("/");
    const commaSyntax = rgbPart.includes(",");
    const parts = rgbPart.trim().split(commaSyntax ? /\s*,\s*/ : /\s+/);
    const channels = parts.slice(0, 3);
    const alpha = alphaPart?.trim() ?? (commaSyntax ? parts[3] : undefined) ?? "1";
    const numbers = [...channels, alpha];
    const expectedParts = commaSyntax && alphaPart === undefined ? [3, 4] : [3];
    const valid = extra === undefined && !(commaSyntax && alphaPart !== undefined)
      && expectedParts.includes(parts.length) && channels.length === 3
      && numbers.every((part) => /^(?:\d+(?:\.\d*)?|\.\d+)$/.test(part));
    const result = numbers.map(Number);
    if (valid && result.every(Number.isFinite)
      && result.slice(0, 3).every((channel) => channel >= 0 && channel <= 255)
      && result[3] >= 0 && result[3] <= 1) return result;
  }
  throw new Error(`cannot parse colour "${value}" for --${token}`);
}

/** Resolve aliases and the opaque sRGB mixes used by Classic, with path-local cycle detection. */
export function colour(name, tokens, base, seen = new Set()) {
  if (seen.has(name)) throw new Error(`circular token reference at --${name}`);
  const raw = tokens[name] ?? base[name];
  if (raw === undefined) throw new Error(`missing token --${name}`);
  return colourValue(raw, tokens, base, new Set([...seen, name]));
}

export function colourValue(value, tokens, base, seen = new Set()) {
  const raw = value.trim();
  const alias = /^var\(\s*--([\w-]+)\s*\)$/.exec(raw);
  if (alias) return colour(alias[1], tokens, base, seen);
  const mix = /^color-mix\(in srgb, (.+) ([\d.]+)%, (.+)\)$/.exec(raw);
  if (!mix) return parseColour(raw, "colour token");
  const fraction = Number(mix[2]) / 100;
  if (!Number.isFinite(fraction) || fraction < 0 || fraction > 1) throw new Error("invalid sRGB mix weight");
  const left = colourValue(mix[1], tokens, base, seen);
  const right = colourValue(mix[3], tokens, base, seen);
  if (left[3] !== 1 || right[3] !== 1) throw new Error("Classic facets must be opaque");
  return left.map((channel, index) => channel * fraction + right[index] * (1 - fraction));
}

export function mixPercent(raw, name) {
  if (!/^(?:\d+(?:\.\d*)?|\.\d+)%$/.test(raw ?? "")) throw new Error(`invalid percentage --${name}: ${raw}`);
  const fraction = Number(raw.slice(0, -1)) / 100;
  if (fraction > 1) throw new Error(`out-of-range percentage --${name}`);
  return fraction;
}

const linear = (value) => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
const encoded = (value) => value <= .0031308 ? value * 12.92 : 1.055 * value ** (1 / 2.4) - .055;

function toOklab(rgb) {
  const [r, g, b] = rgb.map((channel) => linear(channel / 255));
  const l = Math.cbrt(.4122214708 * r + .5363325363 * g + .0514459929 * b);
  const m = Math.cbrt(.2119034982 * r + .6806995451 * g + .1073969566 * b);
  const s = Math.cbrt(.0883024619 * r + .2817188376 * g + .6299787005 * b);
  return [.2104542553 * l + .793617785 * m - .0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + .4505937099 * s,
    .0259040371 * l + .7827717662 * m - .808675766 * s];
}

function fromOklab([lightness, a, b]) {
  const l = (lightness + .3963377774 * a + .2158037573 * b) ** 3;
  const m = (lightness - .1055613458 * a - .0638541728 * b) ** 3;
  const s = (lightness - .0894841775 * a - 1.291485548 * b) ** 3;
  const rgb = [4.0767416621 * l - 3.3077115913 * m + .2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - .3413193965 * s,
    -.0041960863 * l - .7034186147 * m + 1.707614701 * s];
  const clipped = rgb.map((channel) => Math.max(0, Math.min(255, encoded(channel) * 255)));
  // Current mixes fit CSS Color 4's local-MINDE clipping case; fail closed outside its .02 JND.
  const delta = Math.hypot(...toOklab(clipped).map((value, index) => value - [lightness, a, b][index]));
  if (delta >= .02) throw new Error("oklab mix needs unsupported gamut mapping");
  return clipped;
}

/** CSS Oklab interpolation uses alpha-premultiplied coordinates before converting back to sRGB. */
export function mixOklab(left, right, fraction) {
  if (!Number.isFinite(fraction) || fraction < 0 || fraction > 1) throw new Error("invalid oklab mix weight");
  if (fraction === 0) return [...left];
  if (fraction === 1) return [...right];
  const alpha = left[3] * (1 - fraction) + right[3] * fraction;
  if (alpha === 0) return [0, 0, 0, 0];
  const a = toOklab(left.slice(0, 3));
  const b = toOklab(right.slice(0, 3));
  const coordinates = a.map((channel, index) =>
    (channel * left[3] * (1 - fraction) + b[index] * right[3] * fraction) / alpha);
  return [...fromOklab(coordinates), alpha];
}

export const rgb = (value) => `rgb(${value.slice(0, 3).map((channel) => channel.toFixed(12)).join(" ")}`
  + ` / ${value[3].toFixed(12)})`;

/**
 * Stack backgrounds are opaque; only each incoming layer needs alpha composition.
 * @param {number[]} front
 * @param {number[]} back
 */
export function composite(front, back) {
  const [r, g, b, a] = front;
  const [br, bg, bb] = back;
  return [r * a + br * (1 - a), g * a + bg * (1 - a), b * a + bb * (1 - a), 1];
}

export function contrastRatio(a, b) {
  const luminance = (value) => value.slice(0, 3).map((channel) => linear(channel / 255))
    .reduce((sum, channel, index) => sum + channel * [.2126, .7152, .0722][index], 0);
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + .05) / (lo + .05);
}

export const toHex = (value) => "#" + value.slice(0, 3)
  .map((channel) => Math.round(channel).toString(16).padStart(2, "0")).join("");

export function flattenStack(stack, tokens, base) {
  if (!stack.length) throw new Error("empty background stack");
  const bottom = colour(stack[0], tokens, base);
  if (bottom[3] !== 1) throw new Error(`bottom of stack --${stack[0]} must be opaque`);
  return stack.slice(1).reduce((back, name) => composite(colour(name, tokens, base), back), bottom);
}

/** The CLI and threshold regression share this measurement boundary. */
export function measurePairing(pairing, tokens, base) {
  const bg = flattenStack(pairing.bg, tokens, base);
  const fg = composite(colour(pairing.fg, tokens, base), bg);
  const ratio = contrastRatio(fg, bg);
  if (!Number.isFinite(ratio)) throw new Error("non-finite contrast ratio");
  return { fg, bg, ratio };
}

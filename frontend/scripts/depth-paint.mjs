/** Bounded evaluator for the production Depth paint grammar, not a CSS engine. */
import { colourValue, mixOklab, mixPercent } from "./contrast-colour.mjs";

export function splitPaint(value, delimiter = ",") {
  let depth = 0, start = 0;
  const parts = [];
  for (let i = 0; i < value.length; i += 1) {
    if (value[i] === "(") depth += 1;
    if (value[i] === ")") depth -= 1;
    if (value[i] === delimiter && depth === 0) {
      parts.push(value.slice(start, i).trim());
      start = i + 1;
    }
  }
  if (depth !== 0) throw new Error(`unbalanced paint: ${value}`);
  return [...parts, value.slice(start).trim()];
}

export function resolvePaint(value, variables, seen = new Set()) {
  return value.replace(/var\(\s*--([\w-]+)\s*\)/g, (_, name) => {
    if (seen.has(name)) throw new Error(`circular paint variable --${name}`);
    const raw = variables[name];
    if (raw === undefined || raw === "initial") throw new Error(`missing paint variable --${name}`);
    return resolvePaint(raw, variables, new Set([...seen, name]));
  });
}

function percentage(value) {
  const calc = /^calc\(100% - ([\d.]+%)\)$/.exec(value);
  return calc ? 1 - mixPercent(calc[1], "paint") : mixPercent(value, "paint");
}

export function paintColour(value, variables = {}) {
  const raw = resolvePaint(value, variables).replace(/\s+/g, " ").trim();
  if (raw === "transparent") return [0, 0, 0, 0];
  const mix = /^color-mix\(in (oklab|srgb), (.*)\)$/.exec(raw);
  if (!mix) return colourValue(raw, {}, {});
  const parts = splitPaint(mix[2]);
  if (parts.length !== 2) throw new Error(`unsupported paint mix: ${raw}`);
  const parse = (part) => {
    const match = /^(.*) (calc\(100% - [\d.]+%\)|[\d.]+%)$/.exec(part);
    return { colour: paintColour(match ? match[1] : part), weight: match ? percentage(match[2]) : null };
  };
  const [left, right] = parts.map(parse);
  const a = left.weight ?? (right.weight === null ? .5 : 1 - right.weight);
  const b = right.weight ?? 1 - a;
  if (Math.abs(a + b - 1) > 1e-9) throw new Error(`unsupported normalised paint mix: ${raw}`);
  if (mix[1] === "oklab") return mixOklab(left.colour, right.colour, b);
  const alpha = left.colour[3] * a + right.colour[3] * b;
  if (alpha === 0) return [0, 0, 0, 0];
  return [...left.colour.slice(0, 3).map((channel, i) =>
    (channel * left.colour[3] * a + right.colour[i] * right.colour[3] * b) / alpha), alpha];
}

/** Return declared colour stops, including the implicit endpoints of two-stop gradients. */
export function gradientStops(value, variables) {
  const raw = resolvePaint(value, variables).replace(/\s+/g, " ").trim();
  const match = /^linear-gradient\((.*)\)$/.exec(raw);
  if (!match) throw new Error(`unsupported gradient paint: ${raw}`);
  const parts = splitPaint(match[1]);
  if (parts[0] === "in oklab") parts.shift();
  else if (!parts[0].startsWith("color-mix(") && !parts[0].startsWith("rgb(")
    && !parts[0].startsWith("#") && parts[0] !== "transparent") {
    throw new Error(`unsupported gradient direction: ${parts[0]}`);
  }
  return parts.map((part, index) => {
    const stop = /^(.*) ([\d.]+%)$/.exec(part);
    const position = stop ? mixPercent(stop[2], "gradient stop") : index === 0 ? 0 : index === parts.length - 1 ? 1 : NaN;
    if (!Number.isFinite(position)) throw new Error(`unsupported implicit gradient stop: ${part}`);
    return { position, colour: paintColour(stop ? stop[1] : part) };
  });
}

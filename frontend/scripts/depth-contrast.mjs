/** Actual Depth role/fill combinations, without an intermediate overlay representation. */
import { colour, flattenStack, mixOklab, mixPercent, parseBlock, rgb } from "./contrast-colour.mjs";
import { gradientStops, paintColour } from "./depth-paint.mjs";

const ROLES = ["inset", "card", "pane", "float"];
const FILLS = ["surface-card", "surface-inset", "surface-float", "control-bg", "control-disabled-bg", "rail-bg",
  ...["neutral", "info", "success", "warning", "danger"].map((tone) => `status-${tone}-bg`)];
const SURFACES = ["surface-card", "surface-inset", "surface-float", "control-bg", "control-disabled-bg"];
const key = (role, fill) => `depth-${role}-${fill}`;
const TEXT = [["foreground", 4.5], ["contrast-muted", 4.5], ["contrast-helper", 4.5],
  ["contrast-icon-muted", 3], ["action-text", 4.5], ["focus-ring", 3], ["control-border", 3]];
const pairs = (label, bg, text = TEXT) => text.map(([fg, min]) => ({ label: `depth ${label} ${fg}`, fg, bg, min }));

function effectiveTokens(theme, settings) {
  const tokens = { ...theme.tokens, ...settings };
  const foreground = colour("foreground", tokens, theme.base);
  const mix = (fill, role, alpha = 1) => {
    const raw = colour(fill, tokens, theme.base);
    raw[3] *= alpha;
    return rgb(mixOklab(raw, foreground, mixPercent(settings[`depth-mix-${role}`], `depth-mix-${role}`)));
  };
  for (const fill of FILLS) {
    for (const role of ROLES) {
      if (!fill.startsWith("status-") || role === "pane") tokens[key(role, fill)] = mix(fill, role);
    }
  }
  tokens[key("inset", "surface-inset-80")] = mix("surface-inset", "inset", .8);
  return tokens;
}

/** Keep every original semantic pairing; add direct resting and pane-role variants. */
function surfacePairings(original, fill, dark) {
  const resting = (token) => fill(token === "surface-float" ? "float"
    : token.startsWith("control-") || token === "surface-inset" ? "inset" : "card", token);
  const mapped = dark ? original.filter((pair) => pair.bg.some((token) => SURFACES.includes(token)))
    .map((pair) => ({ ...pair, label: `depth resting: ${pair.label}`,
      bg: pair.bg.map((token) => SURFACES.includes(token) ? resting(token) : token) })) : [];
  const panes = (dark ? [["pane", "surface-card"], ["pane", "surface-inset"], ["inset", "surface-card"]]
    : [["pane", "surface-inset"]]).flatMap(([role, name]) => original
    .filter((pair) => pair.min >= 3 && pair.bg.includes("surface-card"))
    .map((pair) => ({ ...pair, label: `depth ${role} ${name}: ${pair.label}`,
      bg: pair.bg.map((token) => token === "surface-card" ? fill(role, name)
        : SURFACES.includes(token) ? resting(token) : token) })));
  return [...mapped, ...panes];
}

function commonPairings(fill) {
  const float = fill("float", "surface-float");
  const result = [
    ...pairs("merge pane", [float, "action-tint"], [["action-text", 4.5], ["status-danger-fg", 4.5],
      ["contrast-muted", 4.5], ["contrast-icon-muted", 3], ["contrast-helper", 4.5],
      ["control-border", 3], ["focus-ring", 3]]),
    ...pairs("person inset", [float, fill("inset", "surface-inset")],
      TEXT.filter(([fg]) => fg !== "action-text")),
    ...[["foreground", "control-bg", 4.5], ["control-placeholder", "control-bg", 4.5],
      ["control-disabled-fg", "control-disabled-bg", 3]].map(([fg, name, min]) => ({
      label: `depth float field ${fg}`, fg, bg: [float, fill("inset", name)], min,
    })),
  ];
  for (const tone of ["info", "success", "warning", "danger"]) {
    result.push(...pairs(`${tone} callout`, [fill("card", "surface-card"), fill("pane", `status-${tone}-bg`)],
      [[`status-${tone}-fg`, 4.5], [`status-${tone}-border`, 1.15]]));
  }
  return result;
}

function darkPairings(fill) {
  const card = fill("pane", "surface-card");
  const float = fill("float", "surface-float");
  const descriptions = [
    ["float card-fill", [fill("float", "surface-card")]],
    ["float control-fill", [fill("float", "control-bg")]],
    ["chat pane", [card, fill("pane", "surface-inset")]],
    ["chat field", [card, fill("inset", "surface-inset")]],
    ["neutral action resting", [card, fill("card", "surface-card")]],
    ["neutral action hover", [card, fill("pane", "surface-inset")]],
    ["neutral action pressed", [card, fill("inset", "surface-inset")]],
  ];
  const text = TEXT.filter(([fg]) => ["foreground", "contrast-helper", "contrast-muted", "contrast-icon-muted"]
    .includes(fg));
  // Nested danger affordances are icons (e.g. IconButton); danger labels on
  // cards and floats retain their original 4.5:1 semantic pairings.
  const result = descriptions.flatMap(([label, bg]) => pairs(label, bg,
    [...TEXT, ["danger-text", 3], ["danger-text-hover", 3], ["action-text-hover", 4.5]]));
  result.push(...pairs("calendar header", [float, fill("inset", "surface-inset")], text),
    ...pairs("inset eighty-percent", [float, fill("inset", "surface-inset-80")], text),
    ...pairs("chat placeholder", [card, fill("inset", "surface-inset")], [["control-placeholder", 4.5]]),
    ...pairs("excluded playing action", [fill("card", "surface-card"),
      fill("inset", "surface-inset-80"), "status-info-bg"], [["foreground", 4.5]]),
    ...pairs("merge field", [float, "action-tint", fill("inset", "control-bg")],
      [["foreground", 4.5], ["control-placeholder", 4.5]]));
  for (const name of ["surface-float", "surface-card", "control-bg"]) {
    result.push(...pairs(`float ${name} tinted hover`, [fill("float", name), "action-tint"],
      [["foreground", 4.5], ["action-tint-fg", 4.5], ["focus-ring", 3]]));
  }
  for (const role of ["pane", "float"]) {
    const rail = fill(role, "rail-bg");
    result.push(...pairs(`${role} rail`, [rail], [["rail-fg", 4.5], ["rail-fg-muted", 4.5], ["focus-ring", 3]]),
      ...pairs(`${role} rail hover`, [rail, "rail-item-hover"], [["rail-fg", 4.5]]),
      ...pairs(`${role} rail active`, [rail, "rail-item-active"], [["rail-item-active-fg", 4.5]]));
  }
  for (const tone of ["info", "success", "warning", "danger"]) {
    result.push(...pairs(`${tone} toast`, [float, `status-${tone}-bg`],
      [["foreground", 4.5], [`status-${tone}-fg`, 4.5]]));
  }
  return result;
}

/** Adapt only Depth app themes; light keeps palette fills and dark uses configured Oklab role mixes. */
export function addDepthThemes(themes, css) {
  const settings = parseBlock(css, 'html[data-style="depth"].dark');
  for (const role of ROLES) mixPercent(settings[`depth-mix-${role}`], `depth-mix-${role}`);
  for (const theme of themes.filter((entry) => entry.name.endsWith("-style-depth"))) {
    const dark = /(?:^|-)dark-style-depth$/.test(theme.name);
    const fill = (role, name) => dark ? key(role, name) : name;
    const original = theme.pairings;
    theme.tokens = dark ? effectiveTokens(theme, settings) : theme.tokens;
    theme.pairings = [...original, ...surfacePairings(original, fill, dark), ...commonPairings(fill),
      ...(dark ? darkPairings(fill) : [])].map((pair) => ({ ...pair,
      fg: dark && pair.min === 3 && ["action", "danger"].includes(pair.fg) ? "depth-filled-edge" : pair.fg,
    }));
    if (dark) {
      theme.behindStack = [fill("pane", "surface-card"), "scrim"];
      theme.floatStack = [fill("float", "surface-float")];
    }
  }
}

const GRADIENT_FILLS = new Set(["surface-card", "surface-inset", "surface-inset-80", "surface-float", "control-bg"]);

function gradientModel(theme, css, dark) {
  const settings = parseBlock(css, `html[data-style="depth"]${dark ? ".dark" : ":not(.dark)"}`);
  const tokens = { ...theme.tokens, ...Object.fromEntries(Object.entries(settings)
    .filter(([name]) => !name.startsWith("depth-"))) };
  const put = (name, value) => {
    const token = `depth-gradient-${name}`;
    tokens[token] = rgb(value);
    return token;
  };
  // Values only: selector eligibility and state contracts live in Vitest.
  const variables = { ...theme.base, ...tokens, ...Object.fromEntries(
    [...css.matchAll(/--(depth-[\w-]+)\s*:\s*([^;]+);/g)].map(([, name, value]) => [name, value.trim()])) };
  const shades = [...css.matchAll(/--depth-light-shade:\s*([^;]+);/g)].map((match) => match[1]);
  const cache = new Map();

  function painted(paint, label, original, local = {}) {
    const values = { ...variables, ...local };
    const stops = gradientStops(paint, values);
    const options = stops.map((stop, index) => [...(dark ? original : []), put(`${label}-${index}`, stop.colour)]);
    if (!dark) {
      options.push([...options[0], put(`${label}-highlight`, paintColour("var(--depth-light-highlight)", values))]);
    }
    return options;
  }

  function raised(role, fill, original) {
    const name = `${role}-${fill}`;
    const identity = dark ? `${name}:${original.join(",")}` : name;
    if (!cache.has(identity)) {
      const shade = role === "pane" && fill.startsWith("surface-inset") ? shades[3]
        : shades[{ card: 0, pane: 1, float: 2 }[role]];
      const local = { "depth-light-fill": fill === "surface-inset-80"
        ? "color-mix(in oklab, var(--surface-inset) 80%, transparent)" : `var(--${fill})`,
      "depth-light-shade": shade };
      const paint = dark ? `var(--depth-gradient-${role === "pane" ? "pane-" : ""}paint)`
        : "var(--depth-light-neutral-paint)";
      cache.set(identity, painted(paint, name, original, local));
    }
    return cache.get(identity);
  }

  function actionPaint(token) {
    if (!cache.has(token)) {
      const actionRule = css.slice(css.indexOf("--depth-gradient-action:"));
      const paint = /background-image:\s*([^;]+);/.exec(actionRule)?.[1];
      if (!paint) throw new Error("missing action gradient");
      cache.set(token, painted(paint, token, [], { "depth-gradient-action": `var(--${token})` }));
    }
    return cache.get(token);
  }

  function choices(pair) {
    let result = [[]];
    for (const token of pair.bg) {
      const explicit = /^depth-(inset|card|pane|float)-(.+)$/.exec(token);
      const role = explicit?.[1] ?? ({ "surface-card": "card", "surface-float": "float" })[token];
      const fill = explicit?.[2] ?? token;
      const action = /neutral action/.test(pair.label) && token === pair.bg.at(-1);
      let options = [[token]];
      if (!action && ["card", "pane", "float"].includes(role) && GRADIENT_FILLS.has(fill)) {
        options = raised(role, fill, [token]);
      } else if (dark && ["action", "action-hover", "action-active"].includes(token)
        && !/(disabled|pressed) primary/.test(pair.label)) {
        options = actionPaint(token);
      }
      result = result.flatMap((stack) => options.map((option) => [...stack, ...option]));
    }
    return result;
  }
  return { tokens, choices, put };
}

/** Actual light roles that cannot be inferred from a fill token alone. */
function lightGradientPairings(theme) {
  const fill = (role, name) => {
    const token = key(role, name);
    theme.tokens[token] = rgb(colour(name === "surface-inset-80" ? "surface-inset" : name,
      theme.tokens, theme.base));
    if (name === "surface-inset-80") {
      const raw = colour("surface-inset", theme.tokens, theme.base);
      raw[3] *= .8;
      theme.tokens[token] = rgb(raw);
    }
    return token;
  };
  const pane = fill("pane", "surface-card"), float = fill("float", "surface-float");
  const surfaces = [
    ["float card-fill", [fill("float", "surface-card")]],
    ["float control-fill", [fill("float", "control-bg")]],
    ["chat pane", [pane, fill("pane", "surface-inset")]],
    ["translucent pane", [pane, fill("pane", "surface-inset-80")]],
    ["chat field", [pane, "surface-inset"]],
    ["merge field", [float, "action-tint", "control-bg"]],
    ["neutral action resting", [pane, "surface-card"]],
    ["neutral action hover", [pane, "surface-inset"]],
    ["neutral action pressed", [pane, "surface-inset"]],
  ];
  return surfaces.flatMap(([label, bg]) => pairs(`gradient ${label}`, bg,
    [...TEXT, ["danger-text", 4.5], ["danger-text-hover", 4.5], ["action-text-hover", 4.5],
      ["action", 3], ["danger", 3]]))
    .concat(pairs("gradient chat placeholder", [pane, "surface-inset"], [["control-placeholder", 4.5]]));
}

/** Only decorative surface edges against a gradient are exempt, never flat rails or series. */
function decorativeGradientEdge(pair) {
  return ["surface-card-border", "surface-float-border", "surface-divider"].includes(pair.fg)
    && pair.bg.some((token) => /^(?:depth-(?:card|pane|float)-)?surface-(?:card|float|inset(?:-80)?)$/.test(token));
}

/** Measure strip/grip paint values together; Vitest checks their state selectors. */
export function splitterPairings(theme, css) {
  const dark = /(?:^|-)dark-style-depth$/.test(theme.name);
  const result = [];
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter((match) => match[1].includes("[data-panel-resize-handle-id]"));
  const value = (predicate, property) => {
    const rule = rules.find((match) => predicate(match[1]));
    const raw = rule && new RegExp(`${property}:\\s*([^;]+);`).exec(rule[2])?.[1];
    if (!raw) throw new Error(`missing splitter ${property}`);
    return raw;
  };
  const vertical = value((selector) => selector.includes(dark ? '].dark ' : ']:not(.dark) '), "--depth-split-fill");
  const variables = { ...theme.base, ...theme.tokens,
    ...parseBlock(css, `html[data-style="depth"]${dark ? ".dark" : ":not(.dark)"}`) };
  for (const direction of ["vertical", "horizontal"]) {
    for (const notes of [false, true]) {
      for (const hover of [false, true]) {
        const label = `splitter ${direction} ${notes ? "notes" : "speakers"} ${hover ? "hover" : "rest"}`;
        const bg = `${label}-background`, fg = `${label}-grip`;
        const strip = hover ? value((s) => s.includes(":hover") && !s.includes("> div"), "background-color")
          : direction === "vertical" && !notes ? vertical : "var(--surface-inset)";
        const grip = value((s) => s.includes("> div") && s.includes(":hover") === hover, "background-color");
        theme.tokens[bg] = rgb(paintColour(strip, variables));
        theme.tokens[fg] = rgb(paintColour(grip, variables));
        result.push({ label, fg, bg: ["surface-page", bg], min: 3 });
      }
    }
  }
  return result;
}

/** Sample production stops, combining independently positioned nested surfaces. */
export function addDepthGradientThemes(themes, css) {
  for (const theme of themes.filter((entry) => entry.name.endsWith("-style-depth"))) {
    const dark = /(?:^|-)dark-style-depth$/.test(theme.name);
    const original = [...theme.pairings, ...(dark ? [] : lightGradientPairings(theme))];
    const model = gradientModel(theme, css, dark);
    theme.tokens = model.tokens;
    theme.pairings = original.filter((pair) => !decorativeGradientEdge(pair)).flatMap((pair) =>
      model.choices(pair).map((bg, index) => ({ ...pair, bg, label: `${pair.label} [gradient ${index}]` })));
    const behind = model.choices({ label: "scrim", bg: theme.behindStack ?? ["surface-page", "surface-card", "scrim"] });
    const floats = model.choices({ label: "float", bg: theme.floatStack ?? ["surface-float"] });
    for (const [index, stack] of floats.entries()) {
      const fg = model.put(`separation-${index}`, flattenStack(stack, theme.tokens, theme.base));
      theme.pairings.push(...behind.map((bg, back) => ({
        label: `gradient float separation ${index}:${back}`, fg, bg, min: 1.15,
      })));
    }
    theme.pairings.push(...splitterPairings(theme, css));
    theme.floats = false;
  }
}

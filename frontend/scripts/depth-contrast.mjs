/** Actual Depth role/fill combinations, without an intermediate overlay representation. */
import { colour, flattenStack, mixOklab, mixPercent, parseBlock, rgb } from "./contrast-colour.mjs";

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

const STOPS = [0, .4, .75, .94, 1];
const GRADIENT_FILLS = new Set(["surface-card", "surface-inset", "surface-inset-80", "surface-float", "control-bg"]);

function gradientModel(theme, css, dark) {
  const settings = parseBlock(css, `html[data-style="depth"]${dark ? ".dark" : ":not(.dark)"}`);
  const tokens = { ...theme.tokens, ...Object.fromEntries(Object.entries(settings)
    .filter(([name]) => !name.startsWith("depth-"))) };
  const read = (name) => colour(name, tokens, theme.base);
  const percent = (name) => mixPercent(settings[name], name);
  const put = (name, value) => {
    const token = `depth-gradient-${name}`;
    tokens[token] = rgb(value);
    return token;
  };
  const foreground = read("foreground");
  const alpha = (value, strength) => [...value.slice(0, 3), value[3] * strength];
  const darkTop = dark ? alpha(foreground, percent("depth-gradient-light")) : null;
  const darkBottom = dark ? alpha(foreground, percent("depth-gradient-shade")) : null;

  function raised(role, fill, original) {
    if (dark) {
      const first = role === "pane" ? mixOklab(darkTop, read("action"), .035) : darkTop;
      return STOPS.map((weight, index) => [...original,
        put(`${role}-wash-${index}`, mixOklab(first, darkBottom, weight))]);
    }
    const raw = read(fill === "surface-inset-80" ? "surface-inset" : fill);
    if (fill === "surface-inset-80") raw[3] *= .8;
    const shade = role === "pane" && fill.startsWith("surface-inset") ? "pane-inset" : role;
    const bottom = mixOklab(raw, foreground, percent(`depth-light-${shade}`));
    const stops = STOPS.map((weight, index) => [put(`${role}-${fill}-${index}`, mixOklab(raw, bottom, weight))]);
    // The highlight occupies only the first pixel, at the top stop.
    return [...stops, [...stops[0], put("highlight", alpha(read("action-on"), .65))]];
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
        const own = read(token), first = mixOklab(own, read("action-hover"), .03);
        options = STOPS.map((weight, index) => [put(`${token}-${index}`, mixOklab(first, own, weight))]);
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

/** Replace only Depth's measurements with all independently positioned gradient stops.
 * Decorative borders retain palette values and have no gradient contrast requirement.
 */
export function addDepthGradientThemes(themes, css) {
  for (const theme of themes.filter((entry) => entry.name.endsWith("-style-depth"))) {
    const dark = /(?:^|-)dark-style-depth$/.test(theme.name);
    const original = [...theme.pairings, ...(dark ? [] : lightGradientPairings(theme))];
    const model = gradientModel(theme, css, dark);
    theme.tokens = model.tokens;
    theme.pairings = original.filter((pair) => pair.min >= 3).flatMap((pair) =>
      model.choices(pair).map((bg, index) => ({ ...pair, bg, label: `${pair.label} [gradient ${index}]` })));
    const behind = model.choices({ label: "scrim", bg: theme.behindStack ?? ["surface-page", "surface-card", "scrim"] });
    const floats = model.choices({ label: "float", bg: theme.floatStack ?? ["surface-float"] });
    for (const [index, stack] of floats.entries()) {
      const fg = model.put(`separation-${index}`, flattenStack(stack, theme.tokens, theme.base));
      theme.pairings.push(...behind.map((bg, back) => ({
        label: `gradient float separation ${index}:${back}`, fg, bg, min: 1.15,
      })));
    }
    // The splitter's grip carries identification; the strip and pane edges are decorative.
    const split = dark ? key("pane", "surface-inset") : "depth-gradient-pane-surface-inset-4";
    theme.pairings.push(...pairs("splitter grip", ["surface-page", split], [["control-border", 3]]),
      ...pairs("splitter hover grip", ["action"], [["action-on", 3]]));
    theme.floats = false;
  }
}

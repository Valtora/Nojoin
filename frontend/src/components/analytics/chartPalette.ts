// Categorical chart colours, resolved to the theme tokens in tokens.css.
//
// Two rules the design depends on, both easy to break by accident:
//
// 1. A slot is assigned by the speaker's fixed position in the list and never
//    cycled. Filtering or reordering the list must not repaint the survivors,
//    because colour identifies a person here, not a rank.
// 2. Past the last slot, speakers fold into one neutral "other" colour rather
//    than wrapping back to slot 1. A repeated hue reads as the same person,
//    which is worse than an honest "everyone else".
export const CHART_SLOT_COUNT = 8;

export const chartColor = (index: number): string =>
  index < CHART_SLOT_COUNT
    ? `var(--chart-${index + 1})`
    : "var(--chart-other)";

/** Stable slot assignment keyed by speaker, in the order the API returned. */
export const buildColorMap = (
  speakerKeys: string[],
): Record<string, string> =>
  Object.fromEntries(
    speakerKeys.map((key, index) => [key, chartColor(index)]),
  );

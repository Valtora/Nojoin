export const DEFAULT_MEETING_EDGE_CONTEXT_LEVEL = 2;

export const MEETING_EDGE_CONTEXT_OPTIONS = [
  {
    value: 1,
    label: "Most Complex",
    description: "Only explain rare jargon, project shorthand, or non-obvious acronyms.",
  },
  {
    value: 2,
    label: "",
    description: "Explain domain-specific or advanced terms, but skip common software and workplace language.",
  },
  {
    value: 3,
    label: "",
    description: "Explain technical or context-heavy terms when a general professional might miss the meaning.",
  },
  {
    value: 4,
    label: "",
    description: "Explain moderately technical terms when a brief clarification would help a non-specialist follow along.",
  },
  {
    value: 5,
    label: "Least Complex",
    description: "Be generous with clarifications for most non-trivial technical or project-specific language.",
  },
] as const;

export const clampMeetingEdgeContextLevel = (value?: number | null) => {
  return Math.min(
    5,
    Math.max(1, value ?? DEFAULT_MEETING_EDGE_CONTEXT_LEVEL),
  );
};
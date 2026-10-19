export interface WhisperModelOption {
  id: string;
  label: string;
  params: string;
  vram: string;
  speed: string;
}

// Shared Whisper catalogue used by the first-run wizard and the settings
// model picker. Keep in sync with WHISPER_MODEL_SIZES_MB on the backend.
export const WHISPER_MODELS: WhisperModelOption[] = [
  { id: "tiny", label: "Tiny", params: "39 M", vram: "~1 GB", speed: "~10x" },
  { id: "base", label: "Base", params: "74 M", vram: "~1 GB", speed: "~7x" },
  { id: "small", label: "Small", params: "244 M", vram: "~2 GB", speed: "~4x" },
  {
    id: "medium",
    label: "Medium",
    params: "769 M",
    vram: "~5 GB",
    speed: "~2x",
  },
  {
    id: "large",
    label: "Large",
    params: "1550 M",
    vram: "~10 GB",
    speed: "1x",
  },
  { id: "turbo", label: "Turbo", params: "809 M", vram: "~6 GB", speed: "~8x" },
];

/**
 * A model's size, memory and speed in words, such as "809 M parameters,
 * about 6 GB of GPU memory, about 8 times as fast as Large".
 */
export function describeWhisperModel(model: WhisperModelOption): string {
  const memory = model.vram.replace("~", "about ");
  const speed =
    model.id === "large"
      ? "the slowest and most accurate"
      : `about ${model.speed.replace("~", "").replace("x", "")} times as fast as Large`;
  return `${model.params} parameters, ${memory} of GPU memory, ${speed}`;
}

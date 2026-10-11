import { describe, expect, it } from "vitest";

import {
  GPU_ASR_WINDOW_MAX_S,
  GPU_ASR_WINDOW_MIN_S,
  parseGpuAsrWindowInput,
} from "./AiTranscriptionSection";

describe("parseGpuAsrWindowInput", () => {
  it("reads an empty field as the default", () => {
    expect(parseGpuAsrWindowInput("")).toBeNull();
    expect(parseGpuAsrWindowInput("   ")).toBeNull();
  });

  it("accepts a number inside the engine's bounds", () => {
    expect(parseGpuAsrWindowInput("60")).toBe(60);
    expect(parseGpuAsrWindowInput(String(GPU_ASR_WINDOW_MIN_S))).toBe(GPU_ASR_WINDOW_MIN_S);
    expect(parseGpuAsrWindowInput(String(GPU_ASR_WINDOW_MAX_S))).toBe(GPU_ASR_WINDOW_MAX_S);
    expect(parseGpuAsrWindowInput("90.5")).toBe(90.5);
  });

  it("refuses what the backend would refuse, so nothing unsaveable is sent", () => {
    expect(parseGpuAsrWindowInput("10")).toBeUndefined();
    expect(parseGpuAsrWindowInput("500")).toBeUndefined();
    expect(parseGpuAsrWindowInput("abc")).toBeUndefined();
    expect(parseGpuAsrWindowInput("-60")).toBeUndefined();
  });
});

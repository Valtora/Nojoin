import { useCallback, useSyncExternalStore } from "react";

import type { CaptureLevels } from "./shared";
import { DEFAULT_CAPTURE_LEVELS } from "./shared";
import type { CaptureController } from "./controller";

export interface WaveformMonitor {
  start: () => void;
  stop: () => void;
}

const normaliseAnalyserLevel = (analyser: AnalyserNode) => {
  const samples = new Uint8Array(analyser.fftSize);
  analyser.getByteTimeDomainData(samples);

  let sumSquares = 0;
  for (const sample of samples) {
    const centered = (sample - 128) / 128;
    sumSquares += centered * centered;
  }

  const rms = Math.sqrt(sumSquares / samples.length);
  return Math.min(100, Math.round(rms * 180));
};

export const createWaveformMonitor = (options: {
  systemAnalyser: AnalyserNode;
  microphoneAnalyser: AnalyserNode;
  mixedAnalyser: AnalyserNode;
  onBeforeLevels?: () => void;
  onLevels: (levels: CaptureLevels) => void;
  requestAnimationFrameFn?: typeof requestAnimationFrame;
  cancelAnimationFrameFn?: typeof cancelAnimationFrame;
}): WaveformMonitor => {
  const requestFrame =
    options.requestAnimationFrameFn ?? requestAnimationFrame;
  const cancelFrame = options.cancelAnimationFrameFn ?? cancelAnimationFrame;
  let frameId = 0;
  let running = false;

  const loop = () => {
    if (!running) {
      return;
    }

    options.onBeforeLevels?.();
    options.onLevels({
      system: normaliseAnalyserLevel(options.systemAnalyser),
      microphone: normaliseAnalyserLevel(options.microphoneAnalyser),
      mixed: normaliseAnalyserLevel(options.mixedAnalyser),
    });
    frameId = requestFrame(loop);
  };

  return {
    start: () => {
      if (running) {
        return;
      }

      running = true;
      frameId = requestFrame(loop);
    },
    stop: () => {
      running = false;
      if (frameId) {
        cancelFrame(frameId);
      }
      options.onLevels(DEFAULT_CAPTURE_LEVELS);
    },
  };
};

const defaultLevels = (): CaptureLevels => DEFAULT_CAPTURE_LEVELS;

/**
 * The controller's current levels, re-rendering on every sample.
 *
 * The controller is an external store, so useSyncExternalStore reads it
 * directly rather than copying each sample into state from an effect. Its
 * state object is replaced on every update, so `levels` is a stable reference
 * between samples and React can tell a new sample from a re-render.
 */
export const useLiveWaveform = (controller: CaptureController | null) => {
  const subscribe = useCallback(
    (onChange: () => void) =>
      controller ? controller.subscribe(onChange) : () => {},
    [controller],
  );
  const getLevels = useCallback(
    () => (controller ? controller.getState().levels : DEFAULT_CAPTURE_LEVELS),
    [controller],
  );
  return useSyncExternalStore(subscribe, getLevels, defaultLevels);
};

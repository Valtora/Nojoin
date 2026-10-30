import type { CaptureLevels } from "./shared";
import { DEFAULT_CAPTURE_LEVELS } from "./shared";

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

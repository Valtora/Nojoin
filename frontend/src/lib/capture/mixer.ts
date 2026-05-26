export interface CaptureMixer {
  context: AudioContext;
  outputStream: MediaStream;
  systemAnalyser: AnalyserNode;
  microphoneAnalyser: AnalyserNode;
  mixedAnalyser: AnalyserNode;
  updateAutomaticGain: () => void;
  getSystemGain: () => number;
  getMicrophoneGain: () => number;
  dispose: () => Promise<void>;
}

export interface CreateCaptureMixerOptions {
  displayStream: MediaStream;
  microphoneStream: MediaStream;
  audioContextFactory?: () => AudioContext;
}

export interface AnalyserMetrics {
  rms: number;
  peak: number;
}

const AUTO_GAIN_MIN = 0.15;
const AUTO_GAIN_MAX = 1.8;
const AUTO_GAIN_TARGET_RMS = 0.12;
const AUTO_GAIN_PEAK_TARGET = 0.88;
const AUTO_GAIN_SILENCE_RMS = 0.008;
const AUTO_GAIN_SILENCE_PEAK = 0.03;
const AUTO_GAIN_REDUCTION_BLEND = 0.3;
const AUTO_GAIN_BOOST_BLEND = 0.04;
const MIX_PEAK_TARGET = 0.9;

export const clampGain = (value: number) => {
  if (Number.isNaN(value)) {
    return 1;
  }

  if (value < 0) {
    return 0;
  }

  if (value > 2) {
    return 2;
  }

  return value;
};

const clampAutomaticGain = (value: number) => {
  if (Number.isNaN(value)) {
    return 1;
  }

  if (value < AUTO_GAIN_MIN) {
    return AUTO_GAIN_MIN;
  }

  if (value > AUTO_GAIN_MAX) {
    return AUTO_GAIN_MAX;
  }

  return value;
};

export const computeAutomaticGainTarget = ({ rms, peak }: AnalyserMetrics) => {
  if (rms < AUTO_GAIN_SILENCE_RMS && peak < AUTO_GAIN_SILENCE_PEAK) {
    return 1;
  }

  const rmsTarget = AUTO_GAIN_TARGET_RMS / Math.max(rms, AUTO_GAIN_SILENCE_RMS);
  const peakTarget = peak > 0 ? AUTO_GAIN_PEAK_TARGET / peak : AUTO_GAIN_MAX;
  return clampAutomaticGain(Math.min(rmsTarget, peakTarget));
};

const smoothAutomaticGain = (currentGain: number, targetGain: number) => {
  const blend = targetGain < currentGain
    ? AUTO_GAIN_REDUCTION_BLEND
    : AUTO_GAIN_BOOST_BLEND;
  return clampAutomaticGain(
    currentGain + (targetGain - currentGain) * blend,
  );
};

const readAnalyserMetrics = (
  analyser: AnalyserNode,
  samples: Uint8Array<ArrayBuffer>,
): AnalyserMetrics => {
  analyser.getByteTimeDomainData(samples);

  let sumSquares = 0;
  let peak = 0;
  for (const sample of samples) {
    const centered = (sample - 128) / 128;
    const magnitude = Math.abs(centered);
    sumSquares += centered * centered;
    if (magnitude > peak) {
      peak = magnitude;
    }
  }

  return {
    rms: Math.sqrt(sumSquares / samples.length),
    peak,
  };
};

export const mixToMonoAmplitude = (
  systemAmplitude: number,
  microphoneAmplitude: number,
  systemGain = 1,
  microphoneGain = 1,
) => {
  const weightedSystem = systemAmplitude * clampGain(systemGain);
  const weightedMicrophone = microphoneAmplitude * clampGain(microphoneGain);
  return (weightedSystem + weightedMicrophone) / 2;
};

const configureAnalyser = (analyser: AnalyserNode) => {
  analyser.fftSize = 256;
  analyser.smoothingTimeConstant = 0.8;
};

export const createCaptureMixer = async (
  options: CreateCaptureMixerOptions,
): Promise<CaptureMixer> => {
  const context = options.audioContextFactory?.() ?? new AudioContext();
  if (context.state === "suspended") {
    await context.resume();
  }

  const destination = context.createMediaStreamDestination();
  const systemSource = context.createMediaStreamSource(options.displayStream);
  const microphoneSource = context.createMediaStreamSource(
    options.microphoneStream,
  );

  const systemAnalyser = context.createAnalyser();
  const microphoneAnalyser = context.createAnalyser();
  const mixedAnalyser = context.createAnalyser();
  configureAnalyser(systemAnalyser);
  configureAnalyser(microphoneAnalyser);
  configureAnalyser(mixedAnalyser);

  const systemGainNode = context.createGain();
  systemGainNode.gain.value = 1;

  const microphoneGainNode = context.createGain();
  microphoneGainNode.gain.value = 1;

  const sourceMerger = context.createChannelMerger(2);

  systemSource.connect(systemAnalyser);
  microphoneSource.connect(microphoneAnalyser);
  systemSource.connect(systemGainNode);
  microphoneSource.connect(microphoneGainNode);
  systemGainNode.connect(sourceMerger, 0, 0);
  microphoneGainNode.connect(sourceMerger, 0, 1);
  sourceMerger.connect(mixedAnalyser);
  mixedAnalyser.connect(destination);

  const systemSamples = new Uint8Array(systemAnalyser.fftSize);
  const microphoneSamples = new Uint8Array(microphoneAnalyser.fftSize);
  const mixedSamples = new Uint8Array(mixedAnalyser.fftSize);

  const updateAutomaticGain = () => {
    const systemMetrics = readAnalyserMetrics(systemAnalyser, systemSamples);
    const microphoneMetrics = readAnalyserMetrics(
      microphoneAnalyser,
      microphoneSamples,
    );
    const mixedMetrics = readAnalyserMetrics(mixedAnalyser, mixedSamples);

    let nextSystemGain = computeAutomaticGainTarget(systemMetrics);
    let nextMicrophoneGain = computeAutomaticGainTarget(microphoneMetrics);

    if (mixedMetrics.peak > MIX_PEAK_TARGET) {
      const reduction = MIX_PEAK_TARGET / mixedMetrics.peak;
      nextSystemGain = Math.min(
        nextSystemGain,
        systemGainNode.gain.value * reduction,
      );
      nextMicrophoneGain = Math.min(
        nextMicrophoneGain,
        microphoneGainNode.gain.value * reduction,
      );
    }

    systemGainNode.gain.value = smoothAutomaticGain(
      systemGainNode.gain.value,
      nextSystemGain,
    );
    microphoneGainNode.gain.value = smoothAutomaticGain(
      microphoneGainNode.gain.value,
      nextMicrophoneGain,
    );
  };

  return {
    context,
    outputStream: destination.stream,
    systemAnalyser,
    microphoneAnalyser,
    mixedAnalyser,
    updateAutomaticGain,
    getSystemGain: () => systemGainNode.gain.value,
    getMicrophoneGain: () => microphoneGainNode.gain.value,
    dispose: async () => {
      mixedAnalyser.disconnect();
      systemAnalyser.disconnect();
      microphoneAnalyser.disconnect();
      systemGainNode.disconnect();
      microphoneGainNode.disconnect();
      sourceMerger.disconnect();
      systemSource.disconnect();
      microphoneSource.disconnect();
      await context.close();
    },
  };
};
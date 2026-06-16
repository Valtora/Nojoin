export interface CaptureMixer {
  context: AudioContext;
  outputStream: MediaStream;
  systemAnalyser: AnalyserNode;
  microphoneAnalyser: AnalyserNode;
  mixedAnalyser: AnalyserNode;
  updateAutomaticGain: () => void;
  applySettings: (settings: Pick<CreateCaptureMixerOptions, "systemGain" | "microphoneGain">) => void;
  getSystemGain: () => number;
  getMicrophoneGain: () => number;
  dispose: () => Promise<void>;
}

export interface CreateCaptureMixerOptions {
  displayStream?: MediaStream | null;
  microphoneStream: MediaStream;
  systemGain?: number;
  microphoneGain?: number;
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
const LOUDER_SOURCE_DOMINANCE_RATIO = 1.15;

export const clampGain = (value: number) => {
  if (Number.isNaN(value)) {
    return 1;
  }

  if (value < 0) {
    return 0;
  }

  if (value > 3) {
    return 3;
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
  const hasDisplayAudio = Boolean(
    options.displayStream &&
      typeof options.displayStream.getAudioTracks === "function" &&
      options.displayStream.getAudioTracks().length > 0,
  );
  const systemSource = hasDisplayAudio
    ? context.createMediaStreamSource(options.displayStream!)
    : context.createGain();
  if (!hasDisplayAudio && "gain" in systemSource) {
    systemSource.gain.value = 0;
  }
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
  systemGainNode.gain.value = clampGain(options.systemGain ?? 1);
  const systemAutomaticGainNode = context.createGain();
  systemAutomaticGainNode.gain.value = 1;

  const microphoneGainNode = context.createGain();
  microphoneGainNode.gain.value = clampGain(options.microphoneGain ?? 1);
  const microphoneAutomaticGainNode = context.createGain();
  microphoneAutomaticGainNode.gain.value = 1;

  const sourceMerger = context.createChannelMerger(2);

  systemSource.connect(systemAnalyser);
  microphoneSource.connect(microphoneAnalyser);
  systemSource.connect(systemGainNode);
  microphoneSource.connect(microphoneGainNode);
  systemGainNode.connect(systemAutomaticGainNode);
  microphoneGainNode.connect(microphoneAutomaticGainNode);
  systemAutomaticGainNode.connect(sourceMerger, 0, 0);
  microphoneAutomaticGainNode.connect(sourceMerger, 0, 1);
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
      const systemDominant =
        systemMetrics.peak >= microphoneMetrics.peak * LOUDER_SOURCE_DOMINANCE_RATIO;
      const microphoneDominant =
        microphoneMetrics.peak >= systemMetrics.peak * LOUDER_SOURCE_DOMINANCE_RATIO;

      if (systemDominant || !microphoneDominant) {
        nextSystemGain = Math.min(
          nextSystemGain,
          systemAutomaticGainNode.gain.value * reduction,
        );
      }
      if (microphoneDominant) {
        nextMicrophoneGain = Math.min(
          nextMicrophoneGain,
          microphoneAutomaticGainNode.gain.value * reduction,
        );
      }
    }

    systemAutomaticGainNode.gain.value = smoothAutomaticGain(
      systemAutomaticGainNode.gain.value,
      nextSystemGain,
    );
    microphoneAutomaticGainNode.gain.value = smoothAutomaticGain(
      microphoneAutomaticGainNode.gain.value,
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
    applySettings: (settings) => {
      systemGainNode.gain.value = clampGain(settings.systemGain ?? 1);
      microphoneGainNode.gain.value = clampGain(settings.microphoneGain ?? 1);
    },
    getSystemGain: () =>
      clampGain(systemGainNode.gain.value * systemAutomaticGainNode.gain.value),
    getMicrophoneGain: () =>
      clampGain(
        microphoneGainNode.gain.value * microphoneAutomaticGainNode.gain.value,
      ),
    dispose: async () => {
      mixedAnalyser.disconnect();
      systemAnalyser.disconnect();
      microphoneAnalyser.disconnect();
      systemGainNode.disconnect();
      microphoneGainNode.disconnect();
      systemAutomaticGainNode.disconnect();
      microphoneAutomaticGainNode.disconnect();
      sourceMerger.disconnect();
      systemSource.disconnect();
      microphoneSource.disconnect();
      await context.close();
    },
  };
};

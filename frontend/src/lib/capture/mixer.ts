export interface CaptureMixer {
  context: AudioContext;
  outputStream: MediaStream;
  systemAnalyser: AnalyserNode;
  microphoneAnalyser: AnalyserNode;
  mixedAnalyser: AnalyserNode;
  setSystemGain: (value: number) => void;
  setMicrophoneGain: (value: number) => void;
  getSystemGain: () => number;
  getMicrophoneGain: () => number;
  dispose: () => Promise<void>;
}

export interface CreateCaptureMixerOptions {
  displayStream: MediaStream;
  microphoneStream: MediaStream;
  systemGain?: number;
  microphoneGain?: number;
  audioContextFactory?: () => AudioContext;
}

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
  systemGainNode.gain.value = clampGain(options.systemGain ?? 1);

  const microphoneGainNode = context.createGain();
  microphoneGainNode.gain.value = clampGain(options.microphoneGain ?? 1);

  const monoBus = context.createGain();
  monoBus.channelCount = 1;
  monoBus.channelCountMode = "explicit";

  systemSource.connect(systemAnalyser);
  microphoneSource.connect(microphoneAnalyser);
  systemSource.connect(systemGainNode);
  microphoneSource.connect(microphoneGainNode);
  systemGainNode.connect(monoBus);
  microphoneGainNode.connect(monoBus);
  monoBus.connect(mixedAnalyser);
  mixedAnalyser.connect(destination);

  return {
    context,
    outputStream: destination.stream,
    systemAnalyser,
    microphoneAnalyser,
    mixedAnalyser,
    setSystemGain: (value: number) => {
      systemGainNode.gain.value = clampGain(value);
    },
    setMicrophoneGain: (value: number) => {
      microphoneGainNode.gain.value = clampGain(value);
    },
    getSystemGain: () => systemGainNode.gain.value,
    getMicrophoneGain: () => microphoneGainNode.gain.value,
    dispose: async () => {
      mixedAnalyser.disconnect();
      systemAnalyser.disconnect();
      microphoneAnalyser.disconnect();
      systemGainNode.disconnect();
      microphoneGainNode.disconnect();
      monoBus.disconnect();
      systemSource.disconnect();
      microphoneSource.disconnect();
      await context.close();
    },
  };
};
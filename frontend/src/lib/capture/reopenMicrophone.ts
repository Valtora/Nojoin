import type { CaptureSettings } from "./shared";

interface ReopenMicrophoneOptions {
  microphoneDeviceId: string | null;
  settings: CaptureSettings;
  mediaDevices?: MediaDevices;
}

const unavailableMessage =
  "The microphone is unavailable. Connect it or choose another microphone in Settings, then try Resume again.";

const describeMicrophoneFailure = (error: unknown): Error => {
  const name = error && typeof error === "object" && "name" in error ? error.name : "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError" || name === "SecurityError") {
    return new Error(
      "Microphone permission was denied. Allow microphone access in your browser settings, then try Resume again.",
      { cause: error },
    );
  }
  if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") {
    return new Error(
      "The microphone is busy or could not start. Close other apps using it, then try Resume again.",
      { cause: error },
    );
  }
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError") {
    return new Error(unavailableMessage, { cause: error });
  }
  return new Error(
    "Nojoin could not reopen the microphone. Check the selected microphone and browser permissions, then try Resume again.",
    { cause: error },
  );
};

/** Only a successful device listing can justify changing the selected input. */
const resolveMicrophoneDevice = async (mediaDevices: MediaDevices, requested: string | null) => {
  if (requested && mediaDevices.enumerateDevices) {
    try {
      const devices = await mediaDevices.enumerateDevices();
      if (!devices.some((device) => device.kind === "audioinput" && device.deviceId === requested)) {
        return { deviceId: null, usedDefault: true };
      }
    } catch {
      // The device may still be available; request its exact id rather than changing inputs.
    }
  }
  return { deviceId: requested, usedDefault: false };
};

const validateMicrophone = (stream: MediaStream, deviceId: string | null) => {
  const audioTrack = stream.getAudioTracks()[0];
  let message: string | null = null;
  if (!audioTrack || audioTrack.readyState !== "live") {
    message = unavailableMessage;
  } else {
    const actualDeviceId = audioTrack.getSettings?.().deviceId;
    if (deviceId && actualDeviceId && actualDeviceId !== deviceId) {
      message =
        "The browser did not grant the selected microphone. Check your microphone in Settings, then try Resume again.";
    }
  }
  if (message) {
    stream.getTracks().forEach((track) => track.stop());
    throw new Error(message);
  }
};

/**
 * Reopens only a released microphone. A missing selected device may fall back to
 * the browser default; permission and busy failures never change the input.
 * The caller owns the returned stream and must stop it on cancellation or teardown.
 */
export const reopenMicrophone = async (
  options: ReopenMicrophoneOptions,
): Promise<{ stream: MediaStream; usedDefault: boolean }> => {
  const mediaDevices = options.mediaDevices ?? navigator.mediaDevices;
  if (!mediaDevices?.getUserMedia) {
    throw new Error("This browser cannot reopen the microphone. Use a browser with microphone capture support.");
  }
  const { deviceId, usedDefault } = await resolveMicrophoneDevice(mediaDevices, options.microphoneDeviceId);
  let stream: MediaStream;
  try {
    stream = await mediaDevices.getUserMedia({
      audio: {
        deviceId: deviceId ? { exact: deviceId } : undefined,
        echoCancellation: options.settings.echoCancellation,
        noiseSuppression: options.settings.noiseSuppression,
        autoGainControl: options.settings.autoGainControl,
      },
    });
  } catch (error: unknown) {
    throw describeMicrophoneFailure(error);
  }
  validateMicrophone(stream, deviceId);
  return { stream, usedDefault };
};

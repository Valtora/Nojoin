import { useEffect, useRef, useState, type RefObject } from "react";
import { getCapturePlaybackUrl, getRecordingStreamUrl } from "@/lib/api";
import { isAbortError } from "@/lib/errors";
import type { Recording } from "@/types";

interface PlaybackEvents {
  onTimeUpdate: () => void;
  onPlay?: () => void;
  onPause?: () => void;
  onEnded?: () => void;
}

const PAUSE_REQUEST = "nojoin:pause-playback";
const SEEK_REQUEST = "nojoin:seek-playback";

/** Explicit pause still cancels resumption when a source reload already paused the element. */
export function pauseAudioPlayback(audio: HTMLAudioElement): void {
  audio.dispatchEvent(new Event(PAUSE_REQUEST));
  audio.pause();
}

/** Seeks, first taking a newer snapshot if one is waiting, so a line past the loaded audio can play. */
export function seekAudioPlayback(audio: HTMLAudioElement, time: number): void {
  audio.dispatchEvent(new CustomEvent(SEEK_REQUEST, { detail: time }));
  audio.currentTime = time;
}

/** Keeps one media element and its listening position as server audio grows. */
export function useAudioPlayback(
  recording: Recording,
  audioRef: RefObject<HTMLAudioElement | null>,
  events: PlaybackEvents,
) {
  const sequence = recording.capture_playback_sequence;
  const growing = recording.has_proxy !== true && sequence != null;
  const source = growing
    ? sequence >= 0 ? getCapturePlaybackUrl(recording.id, sequence) : null
    : recording.has_proxy === false ? null : getRecordingStreamUrl(recording.id);
  const [isPlaying, setIsPlaying] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [duration, setDuration] = useState(recording.duration_seconds || 0);
  const loadedSource = useRef<string | null>(null);
  const wantsPlay = useRef(false);
  const restoring = useRef<{ time: number; rate: number } | null>(null);
  const latest = useRef({ source, growing, events });

  useEffect(() => { latest.current = { source, growing, events }; });

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const play = () => {
      void audio.play().catch((error: unknown) => {
        if (isAbortError(error)) return;
        wantsPlay.current = false;
        setIsPlaying(false);
      });
    };
    const reload = (next: string, time = audio.currentTime) => {
      restoring.current = { time, rate: audio.playbackRate };
      loadedSource.current = next;
      audio.src = next;
      audio.load();
    };
    // Reloading drops the buffer, so a newer snapshot waits until the listener
    // starts playing or nears the end of this one. The final file is taken at once.
    const atSnapshotEnd = () => wantsPlay.current && (audio.ended ||
      (!audio.paused && Number.isFinite(audio.duration) && audio.duration - audio.currentTime <= 2));
    const refresh = (starting = false) => {
      const next = latest.current.source;
      if (!next || next === loadedSource.current || restoring.current) return;
      if (latest.current.growing && loadedSource.current && !starting && !atSnapshotEnd()) return;
      reload(next);
    };
    const durationChanged = () => {
      if (Number.isFinite(audio.duration) && audio.duration > 0) setDuration(audio.duration);
    };
    const metadata = () => {
      durationChanged();
      setHasError(false);
      const saved = restoring.current;
      if (saved) {
        audio.currentTime = Number.isFinite(audio.duration)
          ? Math.min(saved.time, audio.duration) : saved.time;
        audio.playbackRate = saved.rate;
        restoring.current = null;
        latest.current.events.onTimeUpdate();
        if (wantsPlay.current) play();
        else {
          setIsPlaying(false);
          latest.current.events.onPause?.();
        }
      }
      refresh();
    };
    const onPlay = () => {
      wantsPlay.current = true;
      setIsPlaying(true);
      latest.current.events.onPlay?.();
      refresh(true);
    };
    const onPause = () => {
      if (restoring.current || (audio.ended && latest.current.growing)) return;
      wantsPlay.current = false;
      setIsPlaying(false);
      latest.current.events.onPause?.();
    };
    const seekRequested = (event: Event) => {
      const time = (event as CustomEvent<number>).detail;
      if (restoring.current) restoring.current.time = time;
      else if (latest.current.source && latest.current.source !== loadedSource.current) {
        reload(latest.current.source, time);
      }
    };
    const pauseRequested = () => {
      wantsPlay.current = false;
      setIsPlaying(false);
      latest.current.events.onPause?.();
    };
    const ended = () => {
      if (!latest.current.growing) {
        wantsPlay.current = false;
        setIsPlaying(false);
      }
      latest.current.events.onEnded?.();
      refresh();
    };
    const time = () => { latest.current.events.onTimeUpdate(); refresh(); };
    const error = () => {
      restoring.current = null;
      setHasError(true);
      setIsPlaying(false);
    };
    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    audio.addEventListener(PAUSE_REQUEST, pauseRequested);
    audio.addEventListener(SEEK_REQUEST, seekRequested);
    audio.addEventListener("ended", ended);
    audio.addEventListener("timeupdate", time);
    audio.addEventListener("loadedmetadata", metadata);
    audio.addEventListener("durationchange", durationChanged);
    audio.addEventListener("error", error);
    refresh();
    return () => {
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener(PAUSE_REQUEST, pauseRequested);
      audio.removeEventListener(SEEK_REQUEST, seekRequested);
      audio.removeEventListener("ended", ended);
      audio.removeEventListener("timeupdate", time);
      audio.removeEventListener("loadedmetadata", metadata);
      audio.removeEventListener("durationchange", durationChanged);
      audio.removeEventListener("error", error);
    };
  }, [audioRef, source]);

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio || !source) return;
    if (wantsPlay.current) {
      pauseAudioPlayback(audio);
    } else {
      wantsPlay.current = true;
      setIsPlaying(true);
      if (!restoring.current) void audio.play().catch((error: unknown) => {
        if (isAbortError(error)) return;
        wantsPlay.current = false;
        setIsPlaying(false);
      });
    }
  };
  return { isPlaying, hasError, duration, togglePlay, growing, waiting: !source };
}

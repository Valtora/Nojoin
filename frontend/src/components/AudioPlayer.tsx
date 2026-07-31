"use client";

import { Recording, RecordingStatus } from "@/types";
import { getRecordingStreamUrl } from "@/lib/api";
import {
  Play,
  Pause,
  Volume2,
  VolumeX,
  Loader2,
} from "lucide-react";
import { useState, useEffect } from "react";

interface AudioPlayerProps {
  recording: Recording;
  audioRef: React.RefObject<HTMLAudioElement | null>;
  currentTime: number;
  onTimeUpdate: () => void;
  onEnded?: () => void;
  onPlay?: () => void;
  onPause?: () => void;
  compact?: boolean;
}

const formatTime = (seconds: number) => {
  if (!seconds || isNaN(seconds)) return "00:00";
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return `${minutes.toString().padStart(2, "0")}:${remainingSeconds.toString().padStart(2, "0")}`;
};

export default function AudioPlayer({
  recording,
  audioRef,
  currentTime,
  onTimeUpdate,
  onEnded,
  onPlay,
  onPause,
  compact = false,
}: AudioPlayerProps) {
  const [hasError, setHasError] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [duration, setDuration] = useState(recording.duration_seconds || 0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);

  // Sync local playing state with audio element events
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const handlePlay = () => {
      setIsPlaying(true);
      onPlay?.();
    };
    const handlePause = () => {
      setIsPlaying(false);
      onPause?.();
    };
    const handleLoadedMetadata = () => {
      if (audio.duration && !isNaN(audio.duration)) {
        setDuration(audio.duration);
      }
      setHasError(false);
    };
    const handleError = () => {
      console.warn("Audio file failed to load:", audio.src);
      setHasError(true);
      setIsPlaying(false);
    };

    audio.addEventListener("play", handlePlay);
    audio.addEventListener("pause", handlePause);
    audio.addEventListener("loadedmetadata", handleLoadedMetadata);
    audio.addEventListener("error", handleError);
    audio.addEventListener("timeupdate", onTimeUpdate);
    if (onEnded) audio.addEventListener("ended", onEnded);

    return () => {
      audio.removeEventListener("play", handlePlay);
      audio.removeEventListener("pause", handlePause);
      audio.removeEventListener("loadedmetadata", handleLoadedMetadata);
      audio.removeEventListener("error", handleError);
      audio.removeEventListener("timeupdate", onTimeUpdate);
      if (onEnded) audio.removeEventListener("ended", onEnded);
    };
  }, [audioRef, onTimeUpdate, onEnded, onPlay, onPause]);

  const togglePlay = () => {
    if (audioRef.current && !hasError) {
      if (isPlaying) {
        audioRef.current.pause();
      } else {
        audioRef.current.play();
      }
    }
  };

  const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    const time = parseFloat(e.target.value);
    if (audioRef.current && !hasError) {
      audioRef.current.currentTime = time;
    }
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = parseFloat(e.target.value);
    setVolume(val);
    if (audioRef.current && !hasError) {
      audioRef.current.volume = val;
      setIsMuted(val === 0);
    }
  };

  const toggleMute = () => {
    if (audioRef.current && !hasError) {
      const newMuted = !isMuted;
      setIsMuted(newMuted);
      audioRef.current.muted = newMuted;
      if (!newMuted && volume === 0) {
        setVolume(1);
        audioRef.current.volume = 1;
      }
    }
  };

  const changePlaybackRate = () => {
    const rates = [0.5, 1, 1.25, 1.5, 2];
    const nextRateIndex = (rates.indexOf(playbackRate) + 1) % rates.length;
    const nextRate = rates[nextRateIndex];
    setPlaybackRate(nextRate);
    if (audioRef.current && !hasError) {
      audioRef.current.playbackRate = nextRate;
    }
  };

  // Proxy audio not yet available -- disable playback
  // The demo recording ("Welcome to Nojoin") intentionally has no proxy audio
  const isDemo = recording.name === "Welcome to Nojoin";
  const proxyUnavailable =
    recording.has_proxy === false &&
    recording.status !== RecordingStatus.UPLOADING &&
    !isDemo;
  const shellClassName = compact
    ? "w-full rounded-2xl border border-control-border bg-surface-card px-3 py-2.5 shadow-card"
    : "w-full bg-surface-card border border-control-border rounded-lg p-2 md:p-3 flex flex-wrap md:flex-nowrap items-center gap-x-3 gap-y-2 shadow-card";
  const compactMockContent = (
    <div className="flex flex-col gap-2 opacity-30 pointer-events-none filter blur-[1px]">
      <div className="flex items-center justify-between gap-3">
        <button className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-card text-foreground shadow-card">
          <Play className="ml-0.5 h-5 w-5 fill-current" />
        </button>
        <div className="h-5 w-5 rounded-full bg-surface-inset" />
      </div>
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-[11px] font-medium text-contrast-helper">
          <div className="h-3 w-9 shrink-0 rounded bg-surface-inset" />
          <div className="h-2 min-w-0 flex-1 rounded-full bg-surface-inset" />
          <div className="h-3 w-9 shrink-0 rounded bg-surface-inset" />
          <div className="h-3 w-8 shrink-0 rounded bg-surface-inset" />
        </div>
      </div>
    </div>
  );

  // Render the "processing" disabled state if proxy is unavailable
  if (proxyUnavailable) {
    return (
      <div
        id="audio-player"
        className={`${shellClassName} relative overflow-hidden`}
      >
        <div className="absolute inset-0 bg-surface-card z-10 flex items-center justify-center">
          <span className={`flex items-center gap-2 rounded-full border border-status-info-border bg-status-info-bg px-3 py-1 font-medium text-status-info-fg ${compact ? "text-xs" : "text-sm"}`}>
            <Loader2 className="w-4 h-4 animate-spin" />
            Audio is being processed, please wait...
          </span>
        </div>

        {compact ? compactMockContent : (
        <div className="flex items-center gap-x-3 gap-y-2 w-full opacity-30 pointer-events-none filter blur-[1px] flex-wrap md:flex-nowrap">
          {/* Mock Play Button */}
          <button className="w-9 h-9 md:w-10 md:h-10 flex items-center justify-center rounded-full bg-surface-card text-foreground shadow-card shrink-0 order-1">
            <Play className="w-5 h-5 fill-current ml-0.5" />
          </button>
          {/* Mock Timeline */}
          <div className="w-full md:w-auto md:flex-1 flex flex-col justify-center gap-1 order-3 md:order-2 mt-1 md:mt-0">
            <div className="w-full h-2.5 bg-surface-inset rounded-full"></div>
          </div>
          {/* Mock Controls Group */}
          <div className="flex items-center gap-2 md:gap-3 ml-auto md:ml-0 pl-0 md:pl-2 border-l-0 md:border-l border-surface-border order-2 md:order-3">
             <div className="w-8 h-4 bg-surface-inset rounded"></div>
             <div className="w-5 h-5 bg-surface-inset rounded-full"></div>
          </div>
        </div>
        )}
      </div>
    );
  }

  if (hasError || isDemo) {
    return (
      <div
        id="audio-player"
        className={`${shellClassName} relative overflow-hidden`}
      >
        {/* Blurred background visual effect */}
        <div className="absolute inset-0 bg-surface-card z-10 flex items-center justify-center">
          <span className={`flex items-center gap-2 rounded-full border border-action-border bg-action-tint px-3 py-1 font-medium text-action-tint-fg ${compact ? "text-xs" : "text-sm"}`}>
            <VolumeX className="w-4 h-4" />
            This meeting was imported with no audio
          </span>
        </div>

        {/* Disabled UI underneath for visual context */}
        {compact ? compactMockContent : (
        <div className="flex items-center gap-x-3 gap-y-2 w-full opacity-30 pointer-events-none filter blur-[1px] flex-wrap md:flex-nowrap">
          {/* Mock Play Button */}
          <button className="w-9 h-9 md:w-10 md:h-10 flex items-center justify-center rounded-full bg-surface-card text-foreground shadow-card shrink-0 order-1">
            <Play className="w-5 h-5 fill-current ml-0.5" />
          </button>
          {/* Mock Timeline */}
          <div className="w-full md:w-auto md:flex-1 flex flex-col justify-center gap-1 order-3 md:order-2 mt-1 md:mt-0">
            <div className="w-full h-2.5 bg-surface-inset rounded-full"></div>
          </div>
           {/* Mock Controls Group */}
           <div className="flex items-center gap-2 md:gap-3 ml-auto md:ml-0 pl-0 md:pl-2 border-l-0 md:border-l border-surface-border order-2 md:order-3">
             <div className="w-8 h-4 bg-surface-inset rounded"></div>
             <div className="w-5 h-5 bg-surface-inset rounded-full"></div>
          </div>
        </div>
        )}

        {!isDemo && (
          <audio
            ref={audioRef}
            src={getRecordingStreamUrl(recording.id)}
            preload="auto"
            className="hidden"
          />
        )}
      </div>
    );
  }

  return (
    <div
      id="audio-player"
      className={shellClassName}
    >
      <audio
        ref={audioRef}
        src={getRecordingStreamUrl(recording.id)}
        preload="auto"
      />

      {compact ? (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3">
            <button
              onClick={togglePlay}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-action text-foreground shadow-card transition-colors hover:bg-action"
            >
              {isPlaying ? (
                <Pause className="h-5 w-5 fill-current" />
              ) : (
                <Play className="ml-0.5 h-5 w-5 fill-current" />
              )}
            </button>

            <button
              onClick={toggleMute}
              className="shrink-0 text-contrast-helper hover:text-contrast-muted"
              title={isMuted || volume === 0 ? "Unmute" : "Mute"}
            >
              {isMuted || volume === 0 ? (
                <VolumeX className="h-5 w-5" />
              ) : (
                <Volume2 className="h-5 w-5" />
              )}
            </button>
          </div>

          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[11px] font-medium text-contrast-helper">
              <span className="w-9 shrink-0 text-left">{formatTime(currentTime)}</span>
              <input
                type="range"
                min={0}
                max={duration || 100}
                value={Math.min(currentTime, duration || 100)}
                onChange={handleSeek}
                className="h-2 min-w-0 flex-1 cursor-pointer appearance-none rounded-full bg-surface-inset accent-action"
              />
              <span className="w-9 shrink-0 text-right">{formatTime(duration)}</span>
              <button
                onClick={changePlaybackRate}
                className="w-8 shrink-0 text-right text-xs font-bold text-contrast-helper hover:text-action-text"
                title="Playback Speed"
              >
                {playbackRate}x
              </button>
            </div>
          </div>
        </div>
      ) : (
        <>
          {/* Play/Pause Button */}
          <button
            onClick={togglePlay}
            className="w-9 h-9 md:w-10 md:h-10 flex items-center justify-center rounded-full bg-action text-foreground hover:bg-action transition-colors shadow-card shrink-0 order-1"
          >
            {isPlaying ? (
              <Pause className="w-5 h-5 fill-current" />
            ) : (
              <Play className="w-5 h-5 fill-current ml-0.5" />
            )}
          </button>

          {/* Time & Progress */}
          <div className="w-full md:w-auto md:flex-1 flex flex-col justify-center gap-1 order-3 md:order-2 mt-1 md:mt-0">
            <div className="flex justify-between text-xs font-medium text-contrast-helper">
              <span>{formatTime(currentTime)}</span>
              <span>{formatTime(duration)}</span>
            </div>
            <div className="w-full">
              <input
                type="range"
                min={0}
                max={duration || 100}
                value={Math.min(currentTime, duration || 100)}
                onChange={handleSeek}
                className="w-full h-2.5 bg-surface-inset rounded-full appearance-none cursor-pointer accent-action"
              />
            </div>
          </div>

          {/* Controls Group */}
          <div className="flex items-center gap-2 md:gap-3 ml-auto md:ml-0 pl-0 md:pl-2 border-l-0 md:border-l border-surface-border order-2 md:order-3">
            {/* Speed Toggle */}
            <button
              onClick={changePlaybackRate}
              className="text-xs font-bold text-contrast-helper hover:text-action-text w-8 text-center"
              title="Playback Speed"
            >
              {playbackRate}x
            </button>

            {/* Volume */}
            <div className="flex items-center gap-2 group relative">
              <button
                onClick={toggleMute}
                className="text-contrast-helper hover:text-contrast-muted"
              >
                {isMuted || volume === 0 ? (
                  <VolumeX className="w-5 h-5" />
                ) : (
                  <Volume2 className="w-5 h-5" />
                )}
              </button>
              <div className="w-0 overflow-hidden group-hover:w-20 transition-all duration-300 ease-in-out">
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.1}
                  value={isMuted ? 0 : volume}
                  onChange={handleVolumeChange}
                  className="w-20 h-1 bg-surface-inset rounded-lg appearance-none cursor-pointer accent-control-border"
                />
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

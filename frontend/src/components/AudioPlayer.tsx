"use client";

import { ButtonBase, InputBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { Recording, RecordingStatus } from "@/types";
import { useAudioPlayback } from "./useAudioPlayback";
import {
  Play,
  Pause,
  Volume2,
  VolumeX,
  Loader2,
} from "lucide-react";
import { useState } from "react";

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
  const { hasError, isPlaying, duration, togglePlay, growing, waiting } = useAudioPlayback(recording, audioRef, {
    onTimeUpdate, onEnded, onPlay, onPause,
  });
  // A snapshot is replaced as capture grows, so buffering it ahead is wasted.
  const preload = growing ? "metadata" : "auto";
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);

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
  // No audio and no proxy on disk: nothing is being prepared, so say so rather
  // than showing the processing state indefinitely. Only once the recording has
  // settled: while it is in flight its master may not be assembled yet.
  const inFlight =
    recording.status === RecordingStatus.UPLOADING ||
    recording.status === RecordingStatus.PAUSED ||
    recording.status === RecordingStatus.PROCESSING ||
    recording.status === RecordingStatus.QUEUED;
  const audioMissing = recording.has_audio === false && !inFlight && !isDemo &&
    (recording.capture_playback_sequence ?? -1) < 0;
  const proxyUnavailable =
    waiting &&
    !isDemo &&
    !audioMissing;
  const shellClassName = compact
    ? "w-full rounded-2xl border border-control-border bg-surface-card px-3 py-2.5 shadow-card"
    : "w-full bg-surface-card border border-control-border rounded-lg p-2 md:p-2.5 flex flex-wrap md:flex-nowrap items-center gap-x-3 gap-y-2 shadow-card";
  const compactMockContent = (
    <div className="flex flex-col gap-2 opacity-30 pointer-events-none filter blur-[1px]">
      <div className="flex items-center justify-between gap-3">
        <ButtonBase data-ui-fill="card" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-card text-foreground shadow-card">
          <Play className="ml-0.5 h-5 w-5 fill-current" />
        </ButtonBase>
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
      <SurfaceBase
        data-ui-fill="card"
        data-ui-border="full" kind="card"
        id="audio-player"
        className={`${shellClassName} relative overflow-hidden`}
      >
        <audio ref={audioRef} preload={preload} />
        <SurfaceBase data-ui-fill="card" kind="pane" className="absolute inset-0 bg-surface-card z-10 flex items-center justify-center">
          <span className={`flex items-center gap-2 rounded-pill border border-status-info-border bg-status-info-bg px-3 py-1 font-medium text-status-info-fg ${compact ? "text-xs" : "text-sm"}`}>
            <Loader2 className="w-4 h-4 animate-spin" />
            {recording.capture_playback_sequence === -1
              ? "Waiting for uploaded audio..." : "Audio is being processed, please wait..."}
          </span>
        </SurfaceBase>

        {compact ? compactMockContent : (
        <div className="flex items-center gap-x-3 gap-y-2 w-full opacity-30 pointer-events-none filter blur-[1px] flex-wrap md:flex-nowrap">
          {/* Mock Play Button */}
          <ButtonBase data-ui-fill="card" className="w-9 h-9 md:w-10 md:h-10 flex items-center justify-center rounded-full bg-surface-card text-foreground shadow-card shrink-0 order-1">
            <Play className="w-5 h-5 fill-current ml-0.5" />
          </ButtonBase>
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
      </SurfaceBase>
    );
  }

  if (hasError || isDemo || audioMissing) {
    return (
      <SurfaceBase
        data-ui-fill="card"
        data-ui-border="full" kind="card"
        id="audio-player"
        className={`${shellClassName} relative overflow-hidden`}
      >
        {!isDemo && !audioMissing && <audio ref={audioRef} preload={preload} />}
        {/* Blurred background visual effect */}
        <SurfaceBase data-ui-fill="card" kind="pane" className="absolute inset-0 bg-surface-card z-10 flex items-center justify-center">
          <span className={`flex items-center gap-2 rounded-pill border border-action-border bg-action-tint px-3 py-1 font-medium text-action-tint-fg ${compact ? "text-xs" : "text-sm"}`}>
            <VolumeX className="w-4 h-4" />
            {isDemo
              ? "This meeting was imported with no audio"
              : audioMissing
                ? "This recording's audio is not available"
                : "This recording's audio could not be loaded"}
          </span>
        </SurfaceBase>

        {/* Disabled UI underneath for visual context */}
        {compact ? compactMockContent : (
        <div className="flex items-center gap-x-3 gap-y-2 w-full opacity-30 pointer-events-none filter blur-[1px] flex-wrap md:flex-nowrap">
          {/* Mock Play Button */}
          <ButtonBase data-ui-fill="card" className="w-9 h-9 md:w-10 md:h-10 flex items-center justify-center rounded-full bg-surface-card text-foreground shadow-card shrink-0 order-1">
            <Play className="w-5 h-5 fill-current ml-0.5" />
          </ButtonBase>
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

      </SurfaceBase>
    );
  }

  return (
    <SurfaceBase
      data-ui-fill="card"
      data-ui-border="full" kind="card"
      id="audio-player"
      className={shellClassName}
    >
      <audio
        ref={audioRef}
        preload={preload}
      />

      {compact ? (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3">
            <ButtonBase
              data-ui-fill="action"
              data-ui-hover="action-hover" chrome="action"
              onClick={togglePlay}
              aria-label={isPlaying ? "Pause" : "Play"}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-action text-action-on shadow-card transition-colors hover:bg-action-hover"
            >
              {isPlaying ? (
                <Pause className="h-5 w-5 fill-current" />
              ) : (
                <Play className="ml-0.5 h-5 w-5 fill-current" />
              )}
            </ButtonBase>

            <ButtonBase
              onClick={toggleMute}
              className="shrink-0 text-contrast-helper hover:text-contrast-muted"
              title={isMuted || volume === 0 ? "Unmute" : "Mute"}
            >
              {isMuted || volume === 0 ? (
                <VolumeX className="h-5 w-5" />
              ) : (
                <Volume2 className="h-5 w-5" />
              )}
            </ButtonBase>
          </div>

          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[11px] font-medium text-contrast-helper">
              <span className="w-9 shrink-0 text-left">{formatTime(currentTime)}</span>
              <InputBase
                type="range"
                min={0}
                max={duration || 100}
                value={Math.min(currentTime, duration || 100)}
                onChange={handleSeek}
                className="h-2 min-w-0 flex-1 cursor-pointer appearance-none rounded-full bg-surface-inset accent-action"
              />
              <span className="w-9 shrink-0 text-right">{formatTime(duration)}</span>
              <ButtonBase
                onClick={changePlaybackRate}
                className="w-8 shrink-0 text-right text-xs font-bold text-contrast-helper hover:text-action-text"
                title="Playback Speed"
              >
                {playbackRate}x
              </ButtonBase>
            </div>
          </div>
        </div>
      ) : (
        <>
          {/* Play/Pause Button */}
          <ButtonBase
            data-ui-fill="action"
            data-ui-hover="action-hover" chrome="action"
            onClick={togglePlay}
            aria-label={isPlaying ? "Pause" : "Play"}
            className="w-9 h-9 md:w-10 md:h-10 flex items-center justify-center rounded-full bg-action text-action-on hover:bg-action-hover transition-colors shadow-card shrink-0 order-1"
          >
            {isPlaying ? (
              <Pause className="w-5 h-5 fill-current" />
            ) : (
              <Play className="w-5 h-5 fill-current ml-0.5" />
            )}
          </ButtonBase>

          {/* Time & Progress */}
          <div className="w-full md:w-auto md:flex-1 flex flex-col justify-center gap-1 order-3 md:order-2 mt-1 md:mt-0">
            <div className="flex justify-between text-xs font-medium text-contrast-helper">
              <span>{formatTime(currentTime)}</span>
              <span>{formatTime(duration)}</span>
            </div>
            <div className="w-full">
              <InputBase
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
            <ButtonBase
              onClick={changePlaybackRate}
              className="text-xs font-bold text-contrast-helper hover:text-action-text w-8 text-center"
              title="Playback Speed"
            >
              {playbackRate}x
            </ButtonBase>

            {/* Volume */}
            <div className="flex items-center gap-2 group relative">
              <ButtonBase
                onClick={toggleMute}
                className="text-contrast-helper hover:text-contrast-muted"
              >
                {isMuted || volume === 0 ? (
                  <VolumeX className="w-5 h-5" />
                ) : (
                  <Volume2 className="w-5 h-5" />
                )}
              </ButtonBase>
              <div className="w-0 overflow-hidden group-hover:w-20 transition-all duration-300 ease-in-out">
                <InputBase
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
    </SurfaceBase>
  );
}

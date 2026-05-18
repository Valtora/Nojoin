"use client";

import { Recording, RecordingStatus } from "@/types";
import { getRecordingStreamUrl } from "@/lib/api";
import {
  Play,
  Pause,
  Volume2,
  VolumeX,
  Loader2,
  Scissors,
  X,
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
  trimStartS?: number | null;
  trimEndS?: number | null;
  onTrimChange?: (startS: number | null, endS: number | null) => void;
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
  trimStartS,
  trimEndS,
  onTrimChange,
}: AudioPlayerProps) {
  const [hasError, setHasError] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [duration, setDuration] = useState(recording.duration_seconds || 0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);

  // Effective trim window. NULL bound falls back to the full recording.
  const lowerBound = trimStartS ?? 0;
  const upperBound = trimEndS ?? duration;

  // Sync local playing state with audio element events
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const handlePlay = () => {
      // If the playhead sits before the trim start, jump into the window.
      if (trimStartS != null && audio.currentTime < trimStartS) {
        audio.currentTime = trimStartS;
      }
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
      // Clamp the initial playhead into the trim window.
      if (trimStartS != null && audio.currentTime < trimStartS) {
        audio.currentTime = trimStartS;
      }
      setHasError(false);
    };
    const handleError = () => {
      console.warn("Audio file failed to load:", audio.src);
      setHasError(true);
      setIsPlaying(false);
    };
    // Enforce the trim end: pause and clamp when the playhead reaches it.
    const handleTrimBoundary = () => {
      if (trimEndS != null && audio.currentTime >= trimEndS) {
        audio.pause();
        audio.currentTime = trimEndS;
      }
    };

    audio.addEventListener("play", handlePlay);
    audio.addEventListener("pause", handlePause);
    audio.addEventListener("loadedmetadata", handleLoadedMetadata);
    audio.addEventListener("error", handleError);
    audio.addEventListener("timeupdate", handleTrimBoundary);
    audio.addEventListener("timeupdate", onTimeUpdate);
    if (onEnded) audio.addEventListener("ended", onEnded);

    return () => {
      audio.removeEventListener("play", handlePlay);
      audio.removeEventListener("pause", handlePause);
      audio.removeEventListener("loadedmetadata", handleLoadedMetadata);
      audio.removeEventListener("error", handleError);
      audio.removeEventListener("timeupdate", handleTrimBoundary);
      audio.removeEventListener("timeupdate", onTimeUpdate);
      if (onEnded) audio.removeEventListener("ended", onEnded);
    };
  }, [audioRef, onTimeUpdate, onEnded, onPlay, onPause, trimStartS, trimEndS]);

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
      // Clamp the target into the trim window.
      const clamped = Math.min(Math.max(time, lowerBound), upperBound);
      audioRef.current.currentTime = clamped;
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

  // Render the "processing" disabled state if proxy is unavailable
  if (proxyUnavailable) {
    return (
      <div
        id="audio-player"
        className="w-full bg-white dark:bg-gray-800/50 border border-gray-300 dark:border-gray-700 rounded-lg p-2 md:p-3 flex flex-wrap md:flex-nowrap items-center gap-x-3 gap-y-2 shadow-sm relative overflow-hidden"
      >
        <div className="absolute inset-0 bg-white/50 dark:bg-black/50 backdrop-blur-sm z-10 flex items-center justify-center">
          <span className="bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 px-3 py-1 rounded-full text-sm font-medium border border-blue-200 dark:border-blue-800 flex items-center gap-2">
            <Loader2 className="w-4 h-4 animate-spin" />
            Audio is being processed, please wait...
          </span>
        </div>

        <div className="flex items-center gap-x-3 gap-y-2 w-full opacity-30 pointer-events-none filter blur-[1px] flex-wrap md:flex-nowrap">
          {/* Mock Play Button */}
          <button className="w-9 h-9 md:w-10 md:h-10 flex items-center justify-center rounded-full bg-gray-400 text-white shadow-sm shrink-0 order-1">
            <Play className="w-5 h-5 fill-current ml-0.5" />
          </button>
          {/* Mock Timeline */}
          <div className="w-full md:w-auto md:flex-1 flex flex-col justify-center gap-1 order-3 md:order-2 mt-1 md:mt-0">
            <div className="w-full h-2.5 bg-gray-200 rounded-full"></div>
          </div>
          {/* Mock Controls Group */}
          <div className="flex items-center gap-2 md:gap-3 ml-auto md:ml-0 pl-0 md:pl-2 border-l-0 md:border-l border-gray-200 dark:border-gray-700 order-2 md:order-3">
             <div className="w-8 h-4 bg-gray-200 rounded"></div>
             <div className="w-5 h-5 bg-gray-200 rounded-full"></div>
          </div>
        </div>
      </div>
    );
  }

  if (hasError || isDemo) {
    return (
      <div
        id="audio-player"
        className="w-full bg-white dark:bg-gray-800/50 border border-gray-300 dark:border-gray-700 rounded-lg p-2 md:p-3 flex flex-wrap md:flex-nowrap items-center gap-x-3 gap-y-2 shadow-sm relative overflow-hidden"
      >
        {/* Blurred background visual effect */}
        <div className="absolute inset-0 bg-white/50 dark:bg-black/50 backdrop-blur-sm z-10 flex items-center justify-center">
          <span className="bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 px-3 py-1 rounded-full text-sm font-medium border border-orange-200 dark:border-orange-800 flex items-center gap-2">
            <VolumeX className="w-4 h-4" />
            This meeting was imported with no audio
          </span>
        </div>

        {/* Disabled UI underneath for visual context */}
        <div className="flex items-center gap-x-3 gap-y-2 w-full opacity-30 pointer-events-none filter blur-[1px] flex-wrap md:flex-nowrap">
          {/* Mock Play Button */}
          <button className="w-9 h-9 md:w-10 md:h-10 flex items-center justify-center rounded-full bg-gray-400 text-white shadow-sm shrink-0 order-1">
            <Play className="w-5 h-5 fill-current ml-0.5" />
          </button>
          {/* Mock Timeline */}
          <div className="w-full md:w-auto md:flex-1 flex flex-col justify-center gap-1 order-3 md:order-2 mt-1 md:mt-0">
            <div className="w-full h-2.5 bg-gray-200 rounded-full"></div>
          </div>
           {/* Mock Controls Group */}
           <div className="flex items-center gap-2 md:gap-3 ml-auto md:ml-0 pl-0 md:pl-2 border-l-0 md:border-l border-gray-200 dark:border-gray-700 order-2 md:order-3">
             <div className="w-8 h-4 bg-gray-200 rounded"></div>
             <div className="w-5 h-5 bg-gray-200 rounded-full"></div>
          </div>
        </div>

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
      className="w-full bg-white dark:bg-gray-800/50 border border-gray-300 dark:border-gray-700 rounded-lg p-2 md:p-3 flex flex-wrap md:flex-nowrap items-center gap-x-3 gap-y-2 shadow-sm"
    >
      <audio
        ref={audioRef}
        src={getRecordingStreamUrl(recording.id)}
        preload="auto"
      />

      {/* Play/Pause Button */}
      <button
        onClick={togglePlay}
        className="w-9 h-9 md:w-10 md:h-10 flex items-center justify-center rounded-full bg-orange-600 text-white hover:bg-orange-700 transition-colors shadow-sm shrink-0 order-1"
      >
        {isPlaying ? (
          <Pause className="w-5 h-5 fill-current" />
        ) : (
          <Play className="w-5 h-5 fill-current ml-0.5" />
        )}
      </button>

      {/* Time & Progress */}
      <div className="w-full md:w-auto md:flex-1 flex flex-col justify-center gap-1 order-3 md:order-2 mt-1 md:mt-0">
        <div className="flex justify-between text-xs font-medium text-gray-500 dark:text-gray-400">
          <span>{formatTime(currentTime)}</span>
          <span>{formatTime(upperBound || duration)}</span>
        </div>
        <div className="relative w-full">
          <input
            type="range"
            min={lowerBound}
            max={upperBound || 100}
            value={Math.min(Math.max(currentTime, lowerBound), upperBound || 100)}
            onChange={handleSeek}
            className="w-full h-2.5 bg-gray-200 dark:bg-gray-700 rounded-full appearance-none cursor-pointer accent-orange-500"
          />
          {/* Trim markers over the slider track */}
          {duration > 0 && trimStartS != null && (
            <span
              className="absolute top-0 h-2.5 w-0.5 bg-orange-600 pointer-events-none"
              style={{ left: `${(trimStartS / duration) * 100}%` }}
              title={`Trim start ${formatTime(trimStartS)}`}
            />
          )}
          {duration > 0 && trimEndS != null && (
            <span
              className="absolute top-0 h-2.5 w-0.5 bg-orange-600 pointer-events-none"
              style={{ left: `${(trimEndS / duration) * 100}%` }}
              title={`Trim end ${formatTime(trimEndS)}`}
            />
          )}
        </div>
      </div>

      {/* Controls Group */}
      <div className="flex items-center gap-2 md:gap-3 ml-auto md:ml-0 pl-0 md:pl-2 border-l-0 md:border-l border-gray-200 dark:border-gray-700 order-2 md:order-3">
        {/* Trim Controls */}
        {onTrimChange && (
          <div className="flex items-center gap-1 pr-1 md:pr-2 border-r-0 md:border-r border-gray-200 dark:border-gray-700">
            <button
              onClick={() => onTrimChange(currentTime, trimEndS ?? null)}
              className="flex items-center gap-1 text-xs font-medium text-gray-600 dark:text-gray-300 hover:text-orange-500"
              title="Set trim start at the playhead"
            >
              <Scissors className="w-4 h-4" />
              <span className="hidden md:inline">Start</span>
            </button>
            <button
              onClick={() => onTrimChange(trimStartS ?? null, currentTime)}
              className="flex items-center gap-1 text-xs font-medium text-gray-600 dark:text-gray-300 hover:text-orange-500"
              title="Set trim end at the playhead"
            >
              <Scissors className="w-4 h-4 -scale-x-100" />
              <span className="hidden md:inline">End</span>
            </button>
            {(trimStartS != null || trimEndS != null) && (
              <button
                onClick={() => onTrimChange(null, null)}
                className="flex items-center gap-1 text-xs font-medium text-gray-600 dark:text-gray-300 hover:text-orange-500"
                title="Clear trim and restore the full recording"
              >
                <X className="w-4 h-4" />
                <span className="hidden md:inline">Clear</span>
              </button>
            )}
          </div>
        )}

        {/* Speed Toggle */}
        <button
          onClick={changePlaybackRate}
          className="text-xs font-bold text-gray-600 dark:text-gray-300 hover:text-orange-500 w-8 text-center"
          title="Playback Speed"
        >
          {playbackRate}x
        </button>

        {/* Volume */}
        <div className="flex items-center gap-2 group relative">
          <button
            onClick={toggleMute}
            className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
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
              className="w-20 h-1 bg-gray-200 dark:bg-gray-700 rounded-lg appearance-none cursor-pointer accent-gray-500"
            />
          </div>
        </div>
      </div>
    </div>
  );
}

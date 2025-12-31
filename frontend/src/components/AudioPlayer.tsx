"use client";

import { Recording } from "@/types";
import { getRecordingStreamUrl } from "@/lib/api";
import { Play, Pause, Volume2, VolumeX } from "lucide-react";
import { useState, useEffect } from "react";

interface AudioPlayerProps {
  recording: Recording;
  audioRef: React.RefObject<HTMLAudioElement | null>;
  currentTime: number;
  onTimeUpdate: () => void;
  onEnded?: () => void;
  onPlay?: () => void;
  onPause?: () => void;
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

  if (hasError) {
    return (
      <div
        id="audio-player"
        className="w-full bg-gray-50 dark:bg-gray-800/50 border border-gray-300 dark:border-gray-700 rounded-lg p-3 flex items-center justify-center gap-4 shadow-sm relative overflow-hidden"
      >
        {/* Blurred background visual effect */}
        <div className="absolute inset-0 bg-white/50 dark:bg-black/50 backdrop-blur-sm z-10 flex items-center justify-center">
          <span className="bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 px-3 py-1 rounded-full text-sm font-medium border border-orange-200 dark:border-orange-800 flex items-center gap-2">
            <VolumeX className="w-4 h-4" />
            This meeting was imported with no audio
          </span>
        </div>

        {/* Disabled UI underneath for visual context */}
        <div className="flex items-center gap-4 w-full opacity-30 pointer-events-none filter blur-[1px]">
          <button className="w-10 h-10 flex items-center justify-center rounded-full bg-gray-400 text-white">
            <Play className="w-5 h-5 fill-current ml-0.5" />
          </button>
          <div className="flex-1 flex flex-col gap-1">
            <div className="w-full h-2.5 bg-gray-200 rounded-full"></div>
          </div>
        </div>

        <audio
          ref={audioRef}
          src={getRecordingStreamUrl(recording.id)}
          preload="metadata"
          className="hidden"
        />
      </div>
    );
  }

  return (
    <div
      id="audio-player"
      className="w-full bg-white dark:bg-gray-800/50 border border-gray-300 dark:border-gray-700 rounded-lg p-3 flex items-center gap-4 shadow-sm"
    >
      <audio
        ref={audioRef}
        src={getRecordingStreamUrl(recording.id)}
        preload="metadata"
      />

      {/* Play/Pause Button */}
      <button
        onClick={togglePlay}
        className="w-10 h-10 flex items-center justify-center rounded-full bg-orange-600 text-white hover:bg-orange-700 transition-colors shadow-sm shrink-0"
      >
        {isPlaying ? (
          <Pause className="w-5 h-5 fill-current" />
        ) : (
          <Play className="w-5 h-5 fill-current ml-0.5" />
        )}
      </button>

      {/* Time & Progress */}
      <div className="flex-1 flex flex-col justify-center gap-1">
        <div className="flex justify-between text-xs font-medium text-gray-500 dark:text-gray-400">
          <span>{formatTime(currentTime)}</span>
          <span>{formatTime(duration)}</span>
        </div>
        <input
          type="range"
          min={0}
          max={duration || 100}
          value={currentTime}
          onChange={handleSeek}
          className="w-full h-2.5 bg-gray-200 dark:bg-gray-700 rounded-full appearance-none cursor-pointer accent-orange-500"
        />
      </div>

      {/* Controls Group */}
      <div className="flex items-center gap-3 pl-2 border-l border-gray-200 dark:border-gray-700">
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

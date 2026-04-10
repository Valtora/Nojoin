"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowRight,
  FolderOpen,
  Layers3,
  Radio,
  Sparkles,
} from "lucide-react";

import { getRecordings } from "@/lib/api";
import { useServiceStatusStore } from "@/lib/serviceStatusStore";
import { ClientStatus, Recording, RecordingStatus } from "@/types";

import AmbientWorkspace from "./AmbientWorkspace";
import MeetingControls from "./MeetingControls";

function formatRelativeTime(value: string) {
  const timestamp = new Date(value);
  const diff = Date.now() - timestamp.getTime();

  if (Number.isNaN(timestamp.getTime())) {
    return "Recently updated";
  }

  const minutes = Math.round(diff / 60000);
  if (minutes < 1) {
    return "Just now";
  }

  if (minutes < 60) {
    return `${minutes}m ago`;
  }

  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }

  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

function formatDuration(seconds?: number) {
  if (!seconds || seconds <= 0) {
    return "No duration yet";
  }

  const totalSeconds = Math.round(seconds);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);

  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }

  if (minutes > 0) {
    return `${minutes}m`;
  }

  return `${totalSeconds}s`;
}

function getStatusLabel(recording: Recording) {
  if (
    recording.status === RecordingStatus.UPLOADING &&
    recording.client_status !== ClientStatus.UPLOADING
  ) {
    return recording.client_status === ClientStatus.PAUSED
      ? "Paused"
      : "Recording";
  }

  switch (recording.status) {
    case RecordingStatus.PROCESSED:
      return "Ready";
    case RecordingStatus.PROCESSING:
      return "Processing";
    case RecordingStatus.QUEUED:
      return "Queued";
    case RecordingStatus.UPLOADING:
      return "Uploading";
    case RecordingStatus.ERROR:
      return "Error";
    case RecordingStatus.CANCELLED:
      return "Cancelled";
    default:
      return "Recorded";
  }
}

function getStatusClasses(recording: Recording) {
  if (
    recording.status === RecordingStatus.UPLOADING &&
    recording.client_status !== ClientStatus.UPLOADING
  ) {
    return "border-red-200 bg-red-50 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300";
  }

  switch (recording.status) {
    case RecordingStatus.PROCESSED:
      return "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300";
    case RecordingStatus.ERROR:
      return "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300";
    default:
      return "border-orange-200 bg-orange-50 text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300";
  }
}

function HealthRow({
  label,
  value,
  detail,
}: {
  label: string;
  value: boolean;
  detail: string;
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-2xl border border-white/60 bg-white/70 px-4 py-3 dark:border-white/10 dark:bg-gray-950/40">
      <div>
        <div className="text-sm font-medium text-gray-900 dark:text-white">
          {label}
        </div>
        <div className="text-xs text-gray-500 dark:text-gray-400">{detail}</div>
      </div>
      <span
        className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${
          value
            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
            : "bg-rose-100 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"
        }`}
      >
        {value ? "Online" : "Offline"}
      </span>
    </div>
  );
}

export default function DashboardHome() {
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [loading, setLoading] = useState(true);
  const {
    backend,
    db,
    worker,
    companion,
    companionAuthenticated,
    companionStatus,
  } = useServiceStatusStore();

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const data = await getRecordings();
        if (!cancelled) {
          setRecordings(data);
        }
      } catch (error) {
        console.error("Failed to load dashboard recordings:", error);
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void load();

    const interval = window.setInterval(load, 30000);
    const handleUpdate = () => {
      void load();
    };

    window.addEventListener("recording-updated", handleUpdate);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.removeEventListener("recording-updated", handleUpdate);
    };
  }, []);

  const recentRecordings = useMemo(
    () =>
      [...recordings]
        .sort(
          (left, right) =>
            new Date(right.updated_at).getTime() -
            new Date(left.updated_at).getTime(),
        )
        .slice(0, 5),
    [recordings],
  );

  const stats = useMemo(() => {
    const live = recordings.filter(
      (recording) =>
        recording.status === RecordingStatus.UPLOADING &&
        recording.client_status !== ClientStatus.UPLOADING,
    ).length;
    const pipeline = recordings.filter((recording) =>
      [
        RecordingStatus.UPLOADING,
        RecordingStatus.QUEUED,
        RecordingStatus.PROCESSING,
      ].includes(recording.status),
    ).length;
    const ready = recordings.filter(
      (recording) => recording.status === RecordingStatus.PROCESSED,
    ).length;

    return { live, pipeline, ready };
  }, [recordings]);

  return (
    <AmbientWorkspace contentClassName="max-w-7xl gap-6">
      <section className="rounded-[2rem] border border-white/60 bg-white/82 p-6 shadow-2xl shadow-orange-950/10 backdrop-blur dark:border-white/10 dark:bg-gray-950/62 dark:shadow-black/20 md:p-8">
        <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr] xl:items-start">
          <div className="space-y-6">
            <div className="space-y-4">
              <span className="inline-flex items-center gap-2 rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
                <Sparkles className="h-3.5 w-3.5" />
                Dashboard
              </span>

              <div className="space-y-3">
                <h1 className="max-w-3xl text-3xl font-semibold tracking-tight text-gray-950 dark:text-white md:text-5xl">
                  Control the day from one place.
                </h1>
                <p className="max-w-2xl text-sm leading-6 text-gray-600 dark:text-gray-300 md:text-base">
                  Start a capture, watch system health, and jump back into the
                  latest meetings without dropping into the recordings workspace
                  until you need it.
                </p>
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-[1.5rem] border border-white/60 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-900/60">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                  Active Library
                </div>
                <div className="mt-2 text-3xl font-semibold text-gray-950 dark:text-white">
                  {loading ? "--" : recordings.length}
                </div>
                <div className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                  Meetings currently in your live workspace.
                </div>
              </div>

              <div className="rounded-[1.5rem] border border-white/60 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-900/60">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                  In Flight
                </div>
                <div className="mt-2 text-3xl font-semibold text-gray-950 dark:text-white">
                  {loading ? "--" : stats.pipeline}
                </div>
                <div className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                  Recording, uploading, queued, or processing right now.
                </div>
              </div>

              <div className="rounded-[1.5rem] border border-white/60 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-900/60">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                  Ready To Review
                </div>
                <div className="mt-2 text-3xl font-semibold text-gray-950 dark:text-white">
                  {loading ? "--" : stats.ready}
                </div>
                <div className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                  Processed meetings ready for transcript and notes work.
                </div>
              </div>
            </div>

            <div className="flex flex-wrap gap-3">
              <Link
                href="/recordings"
                className="inline-flex items-center gap-2 rounded-full bg-orange-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-orange-700"
              >
                Open recordings workspace
                <ArrowRight className="h-4 w-4" />
              </Link>
              <Link
                href="/people"
                className="inline-flex items-center gap-2 rounded-full border border-gray-300 bg-white/80 px-4 py-2.5 text-sm font-semibold text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 dark:border-gray-700 dark:bg-gray-900/70 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
              >
                Review people
              </Link>
              <Link
                href="/settings"
                className="inline-flex items-center gap-2 rounded-full border border-gray-300 bg-white/80 px-4 py-2.5 text-sm font-semibold text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 dark:border-gray-700 dark:bg-gray-900/70 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
              >
                System settings
              </Link>
            </div>
          </div>

          <MeetingControls variant="dashboard" />
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-[2rem] border border-white/60 bg-white/82 p-6 shadow-xl shadow-orange-950/5 backdrop-blur dark:border-white/10 dark:bg-gray-950/62 dark:shadow-black/20">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.2em] text-gray-500 dark:text-gray-400">
                Recent meetings
              </div>
              <h2 className="mt-2 text-2xl font-semibold text-gray-950 dark:text-white">
                Pick up where you left off.
              </h2>
            </div>
            <Link
              href="/recordings"
              className="inline-flex items-center gap-2 text-sm font-medium text-orange-700 transition-colors hover:text-orange-800 dark:text-orange-300 dark:hover:text-orange-200"
            >
              View all
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>

          <div className="mt-6 space-y-3">
            {recentRecordings.length === 0 ? (
              <div className="rounded-[1.5rem] border border-dashed border-orange-200 bg-orange-50/70 p-6 text-sm text-gray-600 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-gray-300">
                No recordings yet. Start a meeting from the capture panel and it
                will appear here.
              </div>
            ) : (
              recentRecordings.map((recording) => (
                <Link
                  key={recording.id}
                  href={`/recordings/${recording.id}`}
                  className="flex items-start justify-between gap-4 rounded-[1.5rem] border border-white/60 bg-white/70 px-4 py-4 transition-colors hover:border-orange-200 hover:bg-orange-50/70 dark:border-white/10 dark:bg-gray-900/60 dark:hover:border-orange-500/20 dark:hover:bg-orange-500/10"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span
                        className={`inline-flex shrink-0 rounded-full border px-2.5 py-1 text-xs font-semibold ${getStatusClasses(
                          recording,
                        )}`}
                      >
                        {getStatusLabel(recording)}
                      </span>
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        {formatRelativeTime(recording.updated_at)}
                      </span>
                    </div>
                    <div className="mt-3 truncate text-base font-semibold text-gray-950 dark:text-white">
                      {recording.name}
                    </div>
                    <div className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                      {recording.processing_step ||
                        `Duration ${formatDuration(recording.duration_seconds)}`}
                    </div>
                  </div>
                  <ArrowRight className="mt-1 h-4 w-4 shrink-0 text-gray-400" />
                </Link>
              ))
            )}
          </div>
        </div>

        <div className="space-y-6">
          <div className="rounded-[2rem] border border-white/60 bg-white/82 p-6 shadow-xl shadow-orange-950/5 backdrop-blur dark:border-white/10 dark:bg-gray-950/62 dark:shadow-black/20">
            <div className="text-xs font-semibold uppercase tracking-[0.2em] text-gray-500 dark:text-gray-400">
              System health
            </div>
            <h2 className="mt-2 text-2xl font-semibold text-gray-950 dark:text-white">
              Platform status at a glance.
            </h2>

            <div className="mt-6 space-y-3">
              <HealthRow
                label="Backend API"
                value={backend}
                detail="Requests, authentication, and orchestration"
              />
              <HealthRow
                label="Database"
                value={db}
                detail="Meeting metadata, transcripts, and notes"
              />
              <HealthRow
                label="Worker"
                value={worker}
                detail="Transcription, diarisation, and note generation"
              />
              <HealthRow
                label="Companion"
                value={companion}
                detail={
                  companion
                    ? companionAuthenticated
                      ? `Authenticated${
                          companionStatus !== "idle"
                            ? ` • ${companionStatus}`
                            : ""
                        }`
                      : "Running but awaiting authorisation"
                    : "Offline or unreachable"
                }
              />
            </div>
          </div>

          <div className="rounded-[2rem] border border-white/60 bg-white/82 p-6 shadow-xl shadow-orange-950/5 backdrop-blur dark:border-white/10 dark:bg-gray-950/62 dark:shadow-black/20">
            <div className="text-xs font-semibold uppercase tracking-[0.2em] text-gray-500 dark:text-gray-400">
              Workflow lanes
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
              <div className="rounded-[1.5rem] border border-white/60 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-900/60">
                <div className="flex items-center gap-3">
                  <div className="rounded-2xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
                    <FolderOpen className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="font-medium text-gray-900 dark:text-white">
                      Recordings workspace
                    </div>
                    <div className="text-sm text-gray-600 dark:text-gray-300">
                      Search, filter, archive, and open meeting records.
                    </div>
                  </div>
                </div>
              </div>

              <div className="rounded-[1.5rem] border border-white/60 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-900/60">
                <div className="flex items-center gap-3">
                  <div className="rounded-2xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
                    <Layers3 className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="font-medium text-gray-900 dark:text-white">
                      Workday hub
                    </div>
                    <div className="text-sm text-gray-600 dark:text-gray-300">
                      This surface is now ready for agenda, planning, and daily
                      execution modules later.
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="mt-6 grid gap-3 sm:grid-cols-2">
              <div className="rounded-[1.5rem] border border-white/60 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-900/60">
                <div className="flex items-center gap-3 text-gray-900 dark:text-white">
                  <Radio className="h-5 w-5 text-orange-600 dark:text-orange-300" />
                  <span className="font-medium">Live capture</span>
                </div>
                <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                  {stats.live > 0
                    ? `${stats.live} meeting${stats.live === 1 ? " is" : "s are"} capturing right now.`
                    : "No meetings are actively recording right now."}
                </p>
              </div>

              <div className="rounded-[1.5rem] border border-white/60 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-900/60">
                <div className="flex items-center gap-3 text-gray-900 dark:text-white">
                  <Activity className="h-5 w-5 text-orange-600 dark:text-orange-300" />
                  <span className="font-medium">Pipeline load</span>
                </div>
                <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                  {stats.pipeline > 0
                    ? `${stats.pipeline} meeting${stats.pipeline === 1 ? " is" : "s are"} somewhere in the processing pipeline.`
                    : "The processing queue is clear at the moment."}
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>
    </AmbientWorkspace>
  );
}
"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Calendar, Clock } from "lucide-react";

import { getRecording, getRecordings } from "@/lib/api";
import { getColorByKey } from "@/lib/constants";
import { ClientStatus, Recording, RecordingStatus } from "@/types";

import AmbientWorkspace from "./AmbientWorkspace";
import DashboardTasksPanel from "./DashboardTasksPanel";
import DashboardUpcomingMeetingsCard from "./DashboardUpcomingMeetingsCard";
import MeetingControls from "./MeetingControls";

function formatMeetingDate(value: string) {
  const timestamp = new Date(value);

  if (Number.isNaN(timestamp.getTime())) {
    return "Unknown date";
  }

  return timestamp.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatMeetingDuration(recording: Recording) {
  if (recording.status === RecordingStatus.UPLOADING) {
    return "--";
  }

  const totalSeconds = Math.floor(recording.duration_seconds || 0);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, "0")}:${seconds
      .toString()
      .padStart(2, "0")}`;
  }

  return `${minutes.toString().padStart(2, "0")}:${seconds
    .toString()
    .padStart(2, "0")}`;
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
    case RecordingStatus.ERROR:
      return "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300";
    default:
      return "border-orange-200 bg-orange-50 text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300";
  }
}

export default function DashboardHome() {
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const data = await getRecordings();
        const recentSummaries = [...data]
          .sort(
            (left, right) =>
              new Date(right.created_at).getTime() -
              new Date(left.created_at).getTime(),
          )
          .slice(0, 5);
        const recentDetails = await Promise.all(
          recentSummaries.map(async (recording) => {
            try {
              return await getRecording(recording.id);
            } catch (detailError) {
              console.error(
                "Failed to load recording details for dashboard:",
                detailError,
              );
              return recording;
            }
          }),
        );

        if (!cancelled) {
          setRecordings(recentDetails);
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
            new Date(right.created_at).getTime() -
            new Date(left.created_at).getTime(),
        ),
    [recordings],
  );

  return (
    <AmbientWorkspace contentClassName="max-w-7xl gap-6">
      <section className="flex flex-col gap-6 xl:grid xl:grid-cols-[minmax(0,1.15fr)_minmax(22rem,0.85fr)] xl:items-start">
        <div className="xl:col-start-1 xl:row-start-1">
          <DashboardUpcomingMeetingsCard />
        </div>

        <div className="flex flex-col gap-6 xl:col-start-2 xl:row-start-1">
          <MeetingControls
            variant="dashboard"
            onMeetingEnd={() => {
              window.dispatchEvent(new Event("recording-updated"));
            }}
          />

          <DashboardTasksPanel />
          <div className="rounded-[2rem] border border-white/60 bg-white/82 p-6 shadow-xl shadow-orange-950/5 backdrop-blur dark:border-white/10 dark:bg-gray-950/62 dark:shadow-black/20">
            <div className="flex items-center justify-between gap-4">
              <div className="mt-2 flex items-start gap-3">
                <div className="rounded-2xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
                  <Clock className="h-5 w-5" />
                </div>
                <h2 className="text-2xl font-semibold text-gray-950 dark:text-white">
                  Recent Meetings
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
              {loading ? (
                <div className="rounded-[1.5rem] border border-dashed border-orange-200 bg-orange-50/70 p-6 text-sm text-gray-600 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-gray-300">
                  Loading meetings...
                </div>
              ) : recentRecordings.length === 0 ? (
                <div className="rounded-[1.5rem] border border-dashed border-orange-200 bg-orange-50/70 p-6 text-sm text-gray-600 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-gray-300">
                  No recordings yet.
                </div>
              ) : (
                recentRecordings.map((recording) => (
                  <Link
                    key={recording.id}
                    href={`/recordings/${recording.id}`}
                    className="flex items-start justify-between gap-4 rounded-[1.5rem] border border-white/60 bg-white/70 px-4 py-4 transition-colors hover:border-orange-200 hover:bg-orange-50/70 dark:border-white/10 dark:bg-gray-900/60 dark:hover:border-orange-500/20 dark:hover:bg-orange-500/10"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-base font-semibold text-gray-950 dark:text-white">
                            {recording.name}
                          </div>
                        </div>

                        {recording.status !== RecordingStatus.PROCESSED && (
                          <span
                            className={`inline-flex shrink-0 rounded-full border px-2.5 py-1 text-xs font-semibold ${getStatusClasses(
                              recording,
                            )}`}
                          >
                            {getStatusLabel(recording)}
                          </span>
                        )}
                      </div>

                      <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-gray-600 dark:text-gray-300">
                        <span className="inline-flex items-center gap-1.5">
                          <Calendar className="h-4 w-4" />
                          {formatMeetingDate(recording.created_at)}
                        </span>
                        <span className="inline-flex items-center gap-1.5">
                          <Clock className="h-4 w-4" />
                          {formatMeetingDuration(recording)}
                        </span>
                      </div>

                      {recording.tags && recording.tags.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-2">
                          {recording.tags.map((tag) => {
                            const color = getColorByKey(tag.color || "gray");

                            return (
                              <span
                                key={tag.id}
                                className="inline-flex items-center rounded-full border border-orange-200 bg-orange-100/85 px-2.5 py-1 text-xs font-semibold text-orange-900 shadow-sm shadow-orange-950/5 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-200"
                              >
                                <span
                                  className={`mr-1.5 h-1.5 w-1.5 rounded-full ${color.dot}`}
                                />
                                {tag.name}
                              </span>
                            );
                          })}
                        </div>
                      )}
                    </div>
                    <ArrowRight className="mt-1 h-4 w-4 shrink-0 text-gray-400" />
                  </Link>
                ))
              )}
            </div>
          </div>
        </div>
      </section>
    </AmbientWorkspace>
  );
}

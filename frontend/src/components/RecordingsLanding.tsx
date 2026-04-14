"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, PlayCircle, Waves, LayoutTemplate } from "lucide-react";

import { getDemoRecording } from "@/lib/api";

import AmbientWorkspace from "./AmbientWorkspace";

export default function RecordingsLanding() {
  const [demoRecordingId, setDemoRecordingId] = useState<number | null>(null);
  const [loadingDemo, setLoadingDemo] = useState(true);

  useEffect(() => {
    let cancelled = false;

    const loadDemoRecording = async () => {
      try {
        const demo = await getDemoRecording();

        if (!cancelled) {
          setDemoRecordingId(demo.id);
        }
      } catch (error) {
        console.error("Failed to load demo recording:", error);
      } finally {
        if (!cancelled) {
          setLoadingDemo(false);
        }
      }
    };

    void loadDemoRecording();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <AmbientWorkspace
      contentClassName="max-w-5xl gap-6"
      paddingClassName="py-6 md:py-8"
    >
      <section
        id="recordings-landing-panel"
        className="rounded-[2rem] border border-white/60 bg-white/82 p-8 shadow-xl shadow-orange-950/5 backdrop-blur dark:border-white/10 dark:bg-gray-950/62 dark:shadow-black/20"
      >
        <div className="flex flex-col gap-8 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-2xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
              <Waves className="h-3.5 w-3.5" />
              Recordings Workspace
            </div>

            <h1 className="mt-4 text-3xl font-semibold tracking-tight text-gray-950 dark:text-white">
              Review meetings, inspect transcripts, and open the guided demo when you are ready.
            </h1>

            <p className="mt-4 text-base leading-7 text-gray-600 dark:text-gray-300">
              Your recordings list stays available in the sidebar, while this page gives first-time users a clean starting point before jumping into a meeting.
            </p>

            <div className="mt-6 grid gap-3 sm:grid-cols-2">
              <div className="rounded-[1.5rem] border border-orange-200 bg-orange-50/80 p-4 dark:border-orange-500/20 dark:bg-orange-500/10">
                <div className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
                  <LayoutTemplate className="h-4 w-4 text-orange-600 dark:text-orange-300" />
                  Use the sidebar
                </div>
                <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
                  Browse recent meetings, archived sessions, and imported audio from the list on the left.
                </p>
              </div>

              <div className="rounded-[1.5rem] border border-orange-200 bg-orange-50/80 p-4 dark:border-orange-500/20 dark:bg-orange-500/10">
                <div className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
                  <PlayCircle className="h-4 w-4 text-orange-600 dark:text-orange-300" />
                  Guided next step
                </div>
                <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
                  Open the seeded Welcome to Nojoin meeting to continue into the transcript walkthrough with a safe demo session.
                </p>
              </div>
            </div>
          </div>

          <div className="w-full max-w-md rounded-[1.75rem] border border-gray-200 bg-gray-50/90 p-5 dark:border-white/10 dark:bg-gray-900/80">
            <h2 className="text-lg font-semibold text-gray-950 dark:text-white">
              Demo meeting
            </h2>
            <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
              The sample meeting is the fastest way to see transcripts, notes, speaker tools, and meeting chat in context.
            </p>

            <div className="mt-5 rounded-[1.5rem] border border-dashed border-orange-200 bg-white/80 p-4 dark:border-orange-500/20 dark:bg-gray-950/70">
              {loadingDemo ? (
                <p className="text-sm text-gray-600 dark:text-gray-300">
                  Checking for the demo meeting...
                </p>
              ) : demoRecordingId ? (
                <div className="space-y-3">
                  <p className="text-sm text-gray-600 dark:text-gray-300">
                    The Welcome to Nojoin meeting is ready.
                  </p>
                  <Link
                    id="recordings-demo-cta"
                    href={`/recordings/${demoRecordingId}`}
                    className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-orange-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-orange-700"
                  >
                    Open Demo Meeting
                    <ArrowRight className="h-4 w-4" />
                  </Link>
                </div>
              ) : (
                <p className="text-sm text-gray-600 dark:text-gray-300">
                  The demo meeting is not available yet. It should appear in the sidebar shortly, or you can re-create it later from Help settings.
                </p>
              )}
            </div>
          </div>
        </div>
      </section>
    </AmbientWorkspace>
  );
}